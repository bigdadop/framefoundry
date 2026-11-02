"""Record real website footage with Playwright + Chromium (read-only by default).

Steps come from (a) a steps file (YAML/JSON list), (b) plain-English instructions, or (c) an automatic
tour (hover the main navigation, scroll section by section). Step actions:
  goto(url) | click(text) | hover/find(text) | scroll(to: down|up|top|bottom|<px>|text:<heading>) |
  type(target, text) | press(key) | wait(seconds) | dismiss (close a popup) | tour
Safety: clicks on elements whose text looks like buy / pay / checkout / subscribe / upgrade / delete / submit /
send / join / sign up / register / generate / log out are SKIPPED (hovered instead) unless that step has
"authorized: true" AND browser.allow_risky_actions is true. Password fields are never typed into. A login
wall is reported as "login_required" (sign in manually first: ./framefoundry.sh login --url ...).
"""
import json
import os
import re
import time

import yaml

from framefoundry.capture.recorder import Shot
from framefoundry.util import FrameFoundryError, info, warn

RISKY = re.compile(r"\b(buy|purchase|pay|payment|checkout|check out|order|place order|subscribe|upgrade|delete|remove|"
                   r"submit|send|confirm|join|sign ?up|register|generate|donate|book now|add to cart|unsubscribe|log ?out|sign ?out|"
                   r"log ?in|sign ?in)\b", re.I)

FIND_JS = r"""
(q) => {
  const words = q.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w => w.length > 2);
  const els = [...document.querySelectorAll('a,button,[role=button],[role=tab],[role=link],h1,h2,h3,h4,label,summary,li,input,textarea,select')];
  const out = [];
  for (const e of els) {
    const r = e.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > 4000) continue;
    const st = getComputedStyle(e); if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
    const t = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim().slice(0, 140);
    if (!t) continue;
    const tl = t.toLowerCase();
    let score = 0;
    if (tl === q.toLowerCase()) score += 10;
    else if (tl.includes(q.toLowerCase())) score += 6;
    for (const w of words) if (tl.includes(w)) score += 1.5;
    if (score <= 0) continue;
    if (/^(A|BUTTON)$/.test(e.tagName) || e.getAttribute('role')) score += 0.5;
    score -= Math.min(2, t.length / 120);
    out.push({score, text: t, x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height,
              tag: e.tagName, href: e.getAttribute('href') || '', type: e.type || '', inForm: !!e.form});
  }
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, 8);
}
"""

OUTLINE_JS = r"""
() => {
  const pick = (sel, n) => [...document.querySelectorAll(sel)].filter(e => { const r = e.getBoundingClientRect(); return r.width > 4 && r.height > 4; })
     .map(e => ({text: (e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 80), href: e.getAttribute('href') || ''}))
     .filter(x => x.text).slice(0, n);
  return {title: document.title, url: location.href, headings: pick('h1,h2,h3', 30), links: pick('a', 60),
          buttons: pick('button,[role=button],[role=tab]', 40), height: document.documentElement.scrollHeight};
}
"""

SCROLLER_JS = """(() => {const c=[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e);
  return /(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+50&&e.clientHeight>300});
  c.sort((a,b)=>b.clientHeight-a.clientHeight); return c[0]||document.scrollingElement})()"""

URL = re.compile(r"(https?://\S+|(?<!\w)/[\w\-/]+)")


def _strip(s):
    s = re.sub(r"^(the|a|an)\s+", "", s.strip().strip(".").strip('"\''), flags=re.I)
    s = re.sub(r"\s+(section|page|tab|button|link|menu|item)$", "", s, flags=re.I)
    return s.strip('"\' ')


def parse_instructions(text, url):
    """Plain-English recording instructions -> steps (offline, rule based)."""
    steps = []
    for raw in re.split(r"[\n;]+|(?<=\.)\s+(?=[A-Z])", text or ""):
        line = re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", raw).strip()
        if not line:
            continue
        low = line.lower()
        m = URL.search(line)
        if re.match(r"^(open|go to|visit|load|navigate to)\b", low) and m:
            steps.append({"action": "goto", "url": m.group(1), "label": line[:60]})
            continue
        if re.match(r"^(open|launch|start at)\s+(the\s+)?(website|site|homepage|home page|landing page)\b", low):
            steps.append({"action": "goto", "url": url, "label": "homepage"})
            continue
        m = re.match(r"^(?:navigate|go)\s+to\s+(.+)$", line, re.I)
        if m:
            steps.append({"action": "click", "text": _strip(m.group(1)), "label": line[:60]})
            continue
        m = re.match(r"^(?:click|press|tap|select|choose)\s+(?:on\s+)?(.+)$", line, re.I)
        if m and not re.match(r"^(enter|escape|esc)$", m.group(1).strip(), re.I):
            steps.append({"action": "click", "text": _strip(m.group(1)), "label": line[:60]})
            continue
        m = re.match(r"^(?:hover|point|move)\s+(?:over|to|at)?\s*(.+)$", line, re.I)
        if m:
            steps.append({"action": "hover", "text": _strip(m.group(1)), "label": line[:60]})
            continue
        m = re.match(r"^type\s+[\"'](.+?)[\"']\s+(?:into|in)\s+(.+)$", line, re.I)
        if m:
            steps.append({"action": "type", "text": m.group(1), "target": _strip(m.group(2)), "label": line[:60]})
            continue
        if re.match(r"^scroll", low):
            to = "bottom" if "bottom" in low or " end" in low else "top" if "top" in low else "up" if " up" in low else "down"
            m = re.search(r"scroll\s+(?:down\s+)?to\s+(?:the\s+)?(.+)$", line, re.I)
            if m and not re.match(r"(bottom|top|end)", m.group(1), re.I):
                to = "text:" + _strip(m.group(1))
            steps.append({"action": "scroll", "to": to, "label": line[:60]})
            continue
        m = re.match(r"^wait\s+(\d+(?:\.\d+)?)", low)
        if m:
            steps.append({"action": "wait", "seconds": float(m.group(1))})
            continue
        if re.match(r"^(close|dismiss)\b.*(popup|pop-up|modal|banner|dialog)", low):
            steps.append({"action": "dismiss", "label": "dismiss popup"})
            continue
        if re.search(r"\b(tour|overview|explore)\b", low):
            steps.append({"action": "tour", "label": "tour"})
            continue
        m = re.match(r"^(?:show|highlight|demonstrate|display|focus on|feature)\s+(?:how\s+)?(.+)$", line, re.I)
        steps.append({"action": "find", "text": _strip(m.group(1)) if m else line, "label": line[:60]})
    return steps


def load_steps(path_or_text, url):
    """A steps file (.yaml/.yml/.json), an instructions .txt file, or literal instructions."""
    if path_or_text and os.path.isfile(path_or_text):
        txt = open(path_or_text, encoding="utf-8").read()
        if path_or_text.endswith((".yaml", ".yml", ".json")):
            data = json.loads(txt) if path_or_text.endswith(".json") else yaml.safe_load(txt)
            steps = data.get("steps") if isinstance(data, dict) else data
            if not isinstance(steps, list):
                raise FrameFoundryError(f"{path_or_text}: expected a list of steps")
            return steps
        return parse_instructions(txt, url)
    if path_or_text:
        return parse_instructions(path_or_text, url)
    return [{"action": "tour", "label": "tour"}]


class Runner:
    def __init__(self, shot, cfg, log):
        self.s, self.cfg, self.log = shot, cfg, log
        self.skipped = []

    def find(self, text, prefer_clickable=False):
        cands = self.s.page.evaluate(FIND_JS, text) or []
        if prefer_clickable:
            cands = [c for c in cands if c["tag"] in ("A", "BUTTON") or c["score"] >= 6] or cands
        return cands[0] if cands else None

    def ensure_visible(self, c):
        vh = self.s.vh
        if c["y"] < 80 or c["y"] > vh - 80:
            cur = self.s.page.evaluate(f"(() => {{const e={SCROLLER_JS}; return e.scrollTop}})()")
            self.s.scroll_el(SCROLLER_JS, max(0, cur + c["y"] - vh * 0.45), 1.0)
            self.s.idle(0.35)
            return True
        return False

    def target(self, text, clickable=False):
        c = self.find(text, clickable)
        if c and self.ensure_visible(c):
            c = self.find(text, clickable) or c
        return c

    def wait_for_content(self, max_s=8.0):
        """Single-page apps often show a splash/loader first: wait until real content (headings, links,
        buttons or text) is rendered, so footage and step marks start on the actual page."""
        t0 = time.time()
        while time.time() - t0 < max_s:
            try:
                n = self.s.page.evaluate("""() => { const vis = e => { const r = e.getBoundingClientRect(); return r.width > 4 && r.height > 4; };
                    return [...document.querySelectorAll('h1,h2,h3,a,button,p')].filter(vis).length; }""")
            except Exception:  # noqa: BLE001 - navigating
                n = 0
            if n >= 4:
                return True
            self.s.idle(0.4)
        return False

    def settle(self, timeout=8000):
        try:
            self.s.page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:  # noqa: BLE001 - busy pages never go idle; that's fine
            pass

    def do(self, st):
        s, a = self.s, st.get("action")
        label = st.get("label") or f"{a}:{st.get('text') or st.get('url') or st.get('to') or ''}"[:60]
        if a == "goto":
            url = st.get("url") or self.cfg["url"]
            if url.startswith("/"):
                url = self.cfg["url"].rstrip("/") + url
            s.page.goto(url, wait_until="domcontentloaded")
            self.settle(12000)
            self.wait_for_content()
            s.privacy()
            s.idle(st.get("seconds", 1.2))
            s.mark(label)
        elif a in ("hover", "find", "show", "highlight"):
            c = self.target(st["text"])
            if not c:
                self.log(f"could not find '{st['text']}' - step skipped")
                self.skipped.append(label)
                return
            s.move(c["x"], c["y"], st.get("move", 0.8))
            s.mark(label)
            s.idle(st.get("seconds", 0.9))
        elif a == "click":
            c = self.target(st["text"], clickable=True)
            if not c:
                self.log(f"could not find '{st['text']}' to click - step skipped")
                self.skipped.append(label)
                return
            m = RISKY.search(c["text"]) or RISKY.search(st["text"])
            submits = c.get("inForm") and c.get("type") in ("submit", "image")
            if (m or submits) and not (st.get("authorized") and self.cfg.get("allow_risky")):
                why = f"looks like '{m.group(0)}'" if m else "it would submit a form"
                self.log(f"SAFETY: not clicking '{c['text'][:40]}' ({why}); hovering instead")
                s.move(c["x"], c["y"], 0.8)
                s.mark(label + " (hover only)")
                s.idle(1.0)
                self.skipped.append(label)
                return
            s.move(c["x"], c["y"], st.get("move", 0.8))
            s.idle(0.15)
            s.mark(label)
            n_pages = len(s.ctx.pages)
            s.click()
            try:
                s.page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            if len(s.ctx.pages) > n_pages:      # a new tab opened: show it in this tab instead
                newp = s.ctx.pages[-1]
                url = newp.url
                newp.close()
                if url and url != "about:blank":
                    s.page.goto(url, wait_until="domcontentloaded")
            self.settle()
            s.privacy()
            s.idle(st.get("seconds", 1.3))
        elif a == "scroll":
            to = st.get("to", "down")
            dur = st.get("seconds", 1.6)
            cur = s.page.evaluate(f"(() => {{const e={SCROLLER_JS}; return [e.scrollTop, e.scrollHeight - e.clientHeight]}})()")
            if to == "bottom":
                y = cur[1]
            elif to == "top":
                y = 0
            elif to == "down":
                y = min(cur[1], cur[0] + s.vh * 0.8)
            elif to == "up":
                y = max(0, cur[0] - s.vh * 0.8)
            elif isinstance(to, (int, float)):
                y = to
            elif isinstance(to, str) and to.startswith("text:"):
                c = self.find(to[5:])
                y = (cur[0] + c["y"] - s.vh * 0.3) if c else cur[0]
            else:
                y = cur[0]
            s.mark(label)
            s.scroll_el(SCROLLER_JS, max(0, min(cur[1], y)), dur)
            s.idle(st.get("hold", 0.6))
        elif a == "type":
            c = self.target(st.get("target") or "") if st.get("target") else None
            if c and c.get("type") == "password":
                self.log("SAFETY: never typing into password fields")
                self.skipped.append(label)
                return
            if c:
                s.move(c["x"], c["y"], 0.7)
            s.click()
            s.idle(0.2)
            s.mark(label)
            s.page.keyboard.type(st.get("text", ""), delay=st.get("delay", 55))
            s.idle(0.6)
        elif a == "press":
            key = st.get("key", "Escape")
            if key == "Enter" and not (st.get("authorized") and self.cfg.get("allow_risky")):
                self.log("SAFETY: Enter can submit forms - skipped (authorise the step to allow it)")
                self.skipped.append(label)
                return
            s.page.keyboard.press(key)
            s.mark(label)
            s.idle(0.6)
        elif a == "dismiss":
            s.page.keyboard.press("Escape")
            s.idle(0.4)
        elif a == "wait":
            s.mark(label)
            s.idle(float(st.get("seconds", 1.0)))
        elif a == "tour":
            self.tour()
        else:
            self.log(f"unknown action '{a}' - skipped")
            self.skipped.append(label)

    def tour(self):
        """Automatic tour: hover the main navigation, then scroll through the page section by section, until
        at least cfg['tour_seconds'] of footage exist (enough for the scenes that will use it)."""
        s = self.s
        t0 = time.time()
        out = s.page.evaluate(OUTLINE_JS)
        for _ in range(16):          # single-page apps show a splash first: wait (<= 8 s) for real content
            if len(out["headings"]) >= 2 or len(out["links"]) >= 3:
                break
            s.idle(0.5)
            out = s.page.evaluate(OUTLINE_JS)
        navs = [l for l in out["links"] if l["text"] and len(l["text"]) < 24 and not RISKY.search(l["text"])][:4]
        for l in navs:
            c = self.find(l["text"], True)
            if c and c["y"] < s.vh * 0.25:
                s.move(c["x"], c["y"], 0.6)
                s.mark(f"nav:{l['text']}")
                s.idle(0.35)
        heads = [h["text"] for h in out["headings"]][:6]
        for h in heads[1:5]:
            c = self.find(h)
            if not c:
                continue
            cur = s.page.evaluate(f"(() => {{const e={SCROLLER_JS}; return e.scrollTop}})()")
            s.mark(f"section:{h[:40]}")
            s.scroll_el(SCROLLER_JS, max(0, cur + c["y"] - s.vh * 0.28), 1.5)
            s.idle(0.4)
            c2 = self.find(h)
            if c2:
                s.move(min(s.vw * 0.7, c2["x"] + 40), c2["y"] + 60, 0.7)
            s.idle(0.8)
        target = float(self.cfg.get("tour_seconds") or 0)
        min_scrolls = 0 if heads[1:] else 3
        for k in range(30):
            if k >= min_scrolls and time.time() - t0 >= target:
                break
            before = s.page.evaluate(f"(() => {{const e={SCROLLER_JS}; return e.scrollTop}})()")
            self.do({"action": "scroll", "to": "down", "seconds": 1.4, "hold": 0.8})
            after = s.page.evaluate(f"(() => {{const e={SCROLLER_JS}; return e.scrollTop}})()")
            if abs(after - before) < 5:      # reached the bottom
                if k >= min_scrolls:
                    break


def record(url, out_dir, cfg_browser, steps=None, name="rec_01", profile=None, private=(), tour_seconds=0.0):
    """Record one shot. Returns metadata dict (status ok | login_required | error).
    tour_seconds: minimum footage length the automatic tour should produce."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright
    if not re.match(r"^https?://", url or ""):
        raise FrameFoundryError(f"--website must be an http(s) URL, got '{url}'")
    os.makedirs(out_dir, exist_ok=True)
    vw, vh = cfg_browser.get("viewport", [1920, 1080])
    cfg = {"url": url, "allow_risky": cfg_browser.get("allow_risky_actions", False), "tour_seconds": tour_seconds}
    steps = list(steps or [{"action": "tour", "label": "tour"}])
    if steps[0].get("action") != "goto":
        steps = [{"action": "goto", "url": url, "label": "open"}] + steps
    logs = []

    def log(m):
        logs.append(m)
        info(f"  [browser] {m}")
    status, err, meta = "ok", None, None
    with sync_playwright() as pw:
        try:
            s = Shot(pw, name, out_dir, profile=profile, dsf=cfg_browser.get("device_scale_factor", 1.5),
                     private=list(private or ()) + list(cfg_browser.get("private_strings") or []), vw=vw, vh=vh,
                     timeout=cfg_browser.get("navigation_timeout", 45))
        except PWError as e:
            raise FrameFoundryError(f"Chromium could not start: {str(e)[:400]}\nRun ./install.sh (it installs Chromium "
                                    "and reports missing system libraries).") from None
        r = Runner(s, cfg, log)
        try:
            s.page.goto("about:blank")
            s.start()
            s.idle(0.3)
            t_start = time.time()
            for k, st in enumerate(steps):
                if time.time() - t_start > cfg_browser.get("max_seconds", 60):
                    log("maximum recording length reached - stopping")
                    break
                r.do(st)
                if k == 0 and not profile and s.page.locator("input[type=password]").count():
                    log("a login form is visible - sign in first with: ./framefoundry.sh login --url " + url)
                    status = "login_required"
            s.idle(0.6)
        except Exception as e:  # noqa: BLE001 - report, keep whatever was captured
            status = "error"
            err = f"{type(e).__name__}: {str(e)[:300]}"
            log(err)
        try:
            meta = s.stop()
        except Exception as e:  # noqa: BLE001
            status, err = "error", (err or "") + f" | could not finalise the recording: {e}"
    if meta:
        meta.update({"title": f"{url} ({name})", "status": status, "skipped": r.skipped, "log": logs, "error": err, "url": url,
                     "usable_start": max(0.0, meta["marks"][0][1] if meta["marks"] else 0.0)})
        json.dump(meta, open(os.path.join(out_dir, f"{name}.json"), "w"), indent=1)
        meta["src"] = os.path.join(out_dir, f"{name}.mp4")
        return meta
    return {"name": name, "status": "error", "error": err or "no frames captured", "log": logs, "url": url}


def explore(url, profile=None):
    """Page outline (title, headings, links, buttons, login wall?) — used to plan steps."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        if profile:
            ctx = pw.chromium.launch_persistent_context(profile, headless=True, viewport={"width": 1920, "height": 1080})
            br = None
        else:
            br = pw.chromium.launch(headless=True)
            ctx = br.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        o = page.evaluate(OUTLINE_JS)
        o["login_wall"] = bool(page.locator("input[type=password]").count())
        ctx.close()
        if br:
            br.close()
    return o


def warn_if_problem(meta):
    if meta.get("status") == "login_required":
        warn("the website showed a login form; footage may show the login page")
    elif meta.get("status") == "error":
        warn(f"website recording problem: {meta.get('error')}")
