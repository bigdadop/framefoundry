"""Playwright screen-recording rig (from the SunoSunao recorder.py, generalised).

* A CDP screencast captures high-quality JPEG frames with timestamps; they are re-timed into a
  constant-30 fps MP4 (CRF 10). Playwright's own .webm recording is kept as the untouched original.
* A visible arrow cursor + click ripple is injected (headless browsers draw none). Cursor positions and
  clicks are logged so the editor's virtual camera can follow the action.
* Privacy: elements containing any configured private string (name, e-mail, ...) are blurred live.
* Nothing here bypasses logins, CAPTCHAs or paywalls, and automation is not hidden from the website.
"""
import base64
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time

from framefoundry.util import ffmpeg_bin

CURSOR_JS = r"""
(() => {
  if (window.__ffCursor) return; window.__ffCursor = true;
  const mk = () => {
    if (!document.body) return setTimeout(mk, 30);
    const c = document.createElement('div'); c.id = '__ff_cursor';
    c.innerHTML = `<svg width="34" height="40" viewBox="0 0 17 20" xmlns="http://www.w3.org/2000/svg">
      <path d="M1 1 L1 16 L5 12.4 L7.6 18.6 L10.4 17.4 L7.8 11.3 L13 11.3 Z" fill="#fff" stroke="#111" stroke-width="1.15" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, {position:'fixed', left:'-100px', top:'-100px', width:'34px', height:'40px',
      zIndex: 2147483647, pointerEvents:'none', filter:'drop-shadow(0 2px 3px rgba(0,0,0,.45))',
      transition:'transform .08s ease-out', transformOrigin:'2px 2px'});
    document.body.appendChild(c);
    const st = document.createElement('style');
    st.textContent = `@keyframes __ffr{from{transform:translate(-50%,-50%) scale(.2);opacity:.9}to{transform:translate(-50%,-50%) scale(1.6);opacity:0}}
      .__ff_ripple{position:fixed;width:46px;height:46px;border-radius:50%;border:3px solid rgba(255,77,141,.95);
      pointer-events:none;z-index:2147483646;animation:__ffr .45s ease-out forwards}
      .__ff_priv{filter:blur(7px) !important}`;
    document.head.appendChild(st);
    window.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    window.addEventListener('mousedown', e => { c.style.transform = 'scale(.85)';
      const r = document.createElement('div'); r.className = '__ff_ripple'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      document.body.appendChild(r); setTimeout(() => r.remove(), 500); }, true);
    window.addEventListener('mouseup', () => { c.style.transform = 'scale(1)'; }, true);
  };
  mk();
})();
"""

PRIVACY_JS = r"""
(secrets) => {
  const apply = () => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n; while ((n = walker.nextNode())) {
      const t = n.nodeValue || '';
      if (secrets.some(s => s && t.includes(s))) {
        let el = n.parentElement; if (!el) continue;
        let box = el; for (let i = 0; i < 3 && box.parentElement; i++) {
          const r = box.parentElement.getBoundingClientRect(); if (r.width > 420 || r.height > 140) break; box = box.parentElement; }
        box.classList.add('__ff_priv');
      }
    }
  };
  apply(); new MutationObserver(apply).observe(document.body, {subtree: true, childList: true, characterData: true});
}
"""


def ease(t):
    return 4 * t * t * t if t < .5 else 1 - pow(-2 * t + 2, 3) / 2


class Shot:
    def __init__(self, pw, name, out_dir, profile=None, dsf=1.5, private=(), vw=1920, vh=1080, quality=92, timeout=45):
        self.name, self.out_dir, self.private, self.dsf = name, out_dir, list(private or ()), dsf
        self.vw, self.vh = vw, vh
        self.raw_dir = os.path.join(out_dir, "_frames_" + name)
        shutil.rmtree(self.raw_dir, ignore_errors=True)
        os.makedirs(self.raw_dir)
        self.native_dir = os.path.join(out_dir, "playwright_native")
        os.makedirs(self.native_dir, exist_ok=True)
        # headless Chromium's screencast ignores emulated deviceScaleFactor; the browser-level scale factor
        # renders real high-DPI frames while the page still lays out at vw x vh CSS pixels
        opts = dict(viewport={"width": vw, "height": vh}, device_scale_factor=1,
                    record_video_dir=self.native_dir, record_video_size={"width": vw, "height": vh})
        args = [f"--force-device-scale-factor={dsf}"] if dsf != 1 else []
        if profile:
            self.ctx = pw.chromium.launch_persistent_context(profile, headless=True, args=args, **opts)
            self.browser = None
        else:
            self.browser = pw.chromium.launch(headless=True, args=args)
            self.ctx = self.browser.new_context(**opts)
        self.ctx.set_default_navigation_timeout(timeout * 1000)
        self.ctx.add_init_script(CURSOR_JS)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.mouse = (vw * 0.62, vh * 0.55)
        self.frames, self.marks, self.cursor, self.clicks = [], [], [], []
        self.lock = threading.Lock()
        self.cdp = None
        self.t0 = None
        self.quality = quality

    # ---- capture -------------------------------------------------------------------------------
    def start(self):
        self.cdp = self.ctx.new_cdp_session(self.page)
        self.q = queue.Queue()
        self.frames = []

        def writer():
            while True:
                item = self.q.get()
                if item is None:
                    break
                idx, data = item
                with open(os.path.join(self.raw_dir, f"{idx:06d}.jpg"), "wb") as f:
                    f.write(base64.b64decode(data))
        self.writer = threading.Thread(target=writer, daemon=True)
        self.writer.start()

        def on_frame(ev):
            try:
                self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
            except Exception:  # noqa: BLE001 - the page may be navigating
                pass
            ts = ev["metadata"].get("timestamp", time.time())
            with self.lock:
                idx = len(self.frames)
                self.frames.append(ts)
            self.q.put((idx, ev["data"]))
        self.cdp.on("Page.screencastFrame", on_frame)
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": self.quality, "everyNthFrame": 1,
                                               "maxWidth": int(self.vw * self.dsf), "maxHeight": int(self.vh * self.dsf)})
        self.t0 = time.time()
        self.cursor.append((0.0, *self.mouse))

    def now(self):
        return time.time() - self.t0 if self.t0 else 0.0

    def mark(self, label):
        self.marks.append((label, round(self.now(), 3)))

    def stop(self):
        self.idle(0.4)
        try:
            self.cdp.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001
            pass
        self.idle(0.3)
        self.q.put(None)
        self.writer.join()
        native = self.page.video.path() if self.page.video else None
        self.ctx.close()
        if self.browser:
            self.browser.close()
        if native and os.path.exists(native):
            shutil.move(native, os.path.join(self.native_dir, f"{self.name}.webm"))
        return self.assemble()

    def assemble(self, fps=30):
        ts = list(self.frames)
        if not ts:
            raise RuntimeError("no frames captured")
        t_first = ts[0]
        dur = ts[-1] - t_first + 0.4
        listfile = os.path.join(self.raw_dir, "concat.txt")
        with open(listfile, "w") as f:
            for i in range(len(ts)):
                d = (ts[i + 1] - ts[i]) if i + 1 < len(ts) else 0.4
                f.write(f"file '{i:06d}.jpg'\nduration {max(d, 1 / 240):.5f}\n")
            f.write(f"file '{len(ts) - 1:06d}.jpg'\n")
        out = os.path.join(self.out_dir, f"{self.name}.mp4")
        subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                        "-vf", f"fps={fps},format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "10", "-g", "15",
                        "-movflags", "+faststart", out], check=True)
        off = t_first - self.t0          # screencast timestamps share the time.time() clock

        def vt(x):
            return round(x - off, 3)
        meta = {"name": self.name, "frames": len(ts), "duration": round(dur, 3), "fps": fps,
                "capture_fps": round(len(ts) / max(dur, 1e-3), 1), "dsf": self.dsf, "css_w": self.vw, "css_h": self.vh,
                "first_frame_offset": round(off, 3),
                "marks": [(lbl, vt(t)) for lbl, t in self.marks],
                "cursor": [(vt(t), round(x, 1), round(y, 1)) for t, x, y in self.cursor if vt(t) >= -0.5],
                "clicks": [(vt(t), round(x, 1), round(y, 1)) for t, x, y in self.clicks]}
        json.dump(meta, open(os.path.join(self.out_dir, f"{self.name}.json"), "w"), indent=1)
        shutil.rmtree(self.raw_dir, ignore_errors=True)
        return meta

    # ---- actions ---------------------------------------------------------------------------------
    def privacy(self):
        if self.private:
            try:
                self.page.evaluate(PRIVACY_JS, self.private)
            except Exception:  # noqa: BLE001
                pass

    def idle(self, s):
        self.page.wait_for_timeout(int(s * 1000))

    def move(self, x, y, dur=0.8):
        """Time-based eased move with a slight arc, logged for the editor's camera."""
        x0, y0 = self.mouse
        t0 = time.time()
        d = math.hypot(x - x0, y - y0)
        last_log = 0.0
        while True:
            k = min(1.0, (time.time() - t0) / dur)
            e = ease(k)
            px, py = x0 + (x - x0) * e, y0 + (y - y0) * e - math.sin(math.pi * e) * 0.06 * d
            self.page.mouse.move(px, py)
            if time.time() - last_log > 0.05:
                self.cursor.append((self.now(), px, py))
                last_log = time.time()
            if k >= 1:
                break
            self.page.wait_for_timeout(8)
        self.mouse = (x, y)
        self.cursor.append((self.now(), x, y))

    def click(self, hold=0.12):
        self.clicks.append((self.now(), *self.mouse))
        self.page.mouse.down()
        self.page.wait_for_timeout(int(hold * 1000))
        self.page.mouse.up()

    def scroll_el(self, selector_js, to_y, dur=1.2):
        self.page.evaluate("""([sel, to, dur]) => new Promise(res => {
            const el = eval(sel) || document.scrollingElement; const from = el.scrollTop; const t0 = performance.now();
            const e = t => t < .5 ? 4*t*t*t : 1 - Math.pow(-2*t+2,3)/2;
            const step = now => { const k = Math.min(1, (now - t0) / (dur*1000)); el.scrollTop = from + (to - from) * e(k);
              k < 1 ? requestAnimationFrame(step) : res(); }; requestAnimationFrame(step); })""", [selector_js, to_y, dur])
        self.cursor.append((self.now(), *self.mouse))
