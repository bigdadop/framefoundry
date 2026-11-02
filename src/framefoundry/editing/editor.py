"""Automatic editor: scene plan + narration word timings + recordings/assets + style profile -> EDL.

The editing grammar of the SunoSunao promo, applied to any script:
  * hook / CTA lines -> kinetic-typography cards: flat background, giant faded topic word, one huge
    keyword per phrase that slams in on its spoken syllable, small words popping in word by word,
    optional stickers and a red "!" on exclamations
  * short punchy lines -> flash words (one centred word at a time)
  * product lines -> real footage (website recordings / uploaded videos) speed-ramped onto the narration,
    with a virtual camera that follows the recorded cursor and punches in on emphasised words and clicks;
    images get a Ken-Burns move
  * whips between footage, hard cuts card <-> footage, logo reveal, end card on the music sting
  * phrase-level pop-in subtitles on footage with emphasised words highlighted
Everything is keyed to word timings, so re-timing the voice re-times the whole edit.
"""
import math
import os
import re

import numpy as np

from framefoundry.rendering import compositor as R

PUNCT_BREAK = re.compile(r"[,.;:!?—–।…]+[\"')\]]*$")
STOP = set("a an the and or but to of in on at for with is are was be it this that your you our my me we us i "
           "just so as by from into its it's than then".split()) | \
    set("ka ki ke hai hain ho se ko mein me ne bhi aur ya to na bas ek yeh ye woh wo".split()) | \
    set("का की के है हैं हो से को में ने भी और या तो ना बस एक यह ये वो".split())


def is_latin(s):
    return all(ord(ch) < 0x250 or 0x2000 <= ord(ch) <= 0x20CF for ch in s)


def core(w):
    return re.sub(r"[^\wऀ-ॿ]", "", w.replace("*", ""), flags=re.UNICODE)


def r3(x):
    return round(float(x), 3)


# ----------------------------------------------------------------------------------- word mapping
def tokens_with_emph(text):
    """'Naam hai *SunoSunao AI.*' -> [('Naam',False),('hai',False),('SunoSunao',True),('AI.',True)]"""
    out, on = [], False
    for w in (text or "").replace("—", " — ").split():
        if w == "—":
            if out:
                out[-1] = (out[-1][0] + " —", out[-1][1])
            continue
        stars = w.count("*")
        emph = on or w.startswith("*") or stars > 0
        if stars % 2 == 1:
            on = not on
        t = w.replace("*", "")
        if t:
            out.append((t, emph))
    return out


def map_display_words(display, spoken, s0, s1):
    """Give each display token (text, emph) a (start, end) from the narration's word timings.
    Equal counts -> 1:1; otherwise proportional by character position across the spoken span
    (handles '₹2,500' displayed vs 'पच्चीस सौ रुपये' spoken)."""
    if not display:
        return []
    if spoken and len(display) == len(spoken):
        return [(t, e, w["s"], w["e"]) for (t, e), w in zip(display, spoken)]
    if spoken:
        lens = np.array([max(1, len(core(w["w"]) or w["w"])) for w in spoken], float)
        cum = np.concatenate([[0], np.cumsum(lens)]) / lens.sum()
        times = [spoken[0]["s"]] + [w["e"] for w in spoken]
    else:
        cum, times = np.array([0, 1.0]), [s0 + 0.05, s1 - 0.05]
    dl = np.array([max(1, len(core(t) or t)) for t, _ in display], float)
    dc = np.concatenate([[0], np.cumsum(dl)]) / dl.sum()
    st = np.interp(dc[:-1], cum, times)
    en = np.interp(dc[1:], cum, times)
    return [(t, e, float(x), float(y)) for (t, e), x, y in zip(display, st, en)]


# ----------------------------------------------------------------------------------- context
class Ctx:
    def __init__(self, plan):
        self.plan = plan
        out = plan["output"]
        self.W, self.H, self.FPS = out["w"], out["h"], out.get("fps", 30)
        R.W, R.H, R.FPS = self.W, self.H, self.FPS
        for name, path in (plan.get("fonts") or {}).items():
            R.FONTS[name] = (path, {})
        self.U = min(self.W, self.H)
        self.vertical = self.H > self.W * 1.05
        self.square = not self.vertical and abs(self.W - self.H) < 0.08 * self.W
        self.style = plan["style"]
        self.brand = plan.get("brand", {})
        self.pal = self.style["palette"]
        self.fonts = self.style["fonts"]
        self.card_i = self.accent_i = self.sticker_i = 0
        self.side = 1
        self.whip_dir = "up"
        self.clips, self.transitions, self.subs = [], [], []
        self.stickers = plan.get("stickers") or []

    def accent(self):
        a = self.pal["accents"][self.accent_i % len(self.pal["accents"])]
        self.accent_i += 1
        return a

    def next_card_colors(self):
        c = self.pal["cards"][self.card_i % len(self.pal["cards"])]
        self.card_i += 1
        return c

    def next_sticker(self):
        if not self.stickers or not self.style["cards"].get("stickers", True):
            return None
        s = self.stickers[self.sticker_i % len(self.stickers)]
        self.sticker_i += 1
        return s


def _lum(rgb):
    r, g, b = [c / 255.0 for c in rgb[:3]]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def needs_logo_plate(path, bg_hex):
    """A light plate behind the logo on a dark card is needed when a real part of the logo (e.g. a dark
    wordmark) would vanish into the background, and it must not be used when the logo has white parts
    (they would vanish into the plate instead)."""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    im.thumbnail((512, 512))
    a = np.asarray(im).astype(np.float32)
    opaque = a[..., 3] > 127
    if opaque.sum() < 10:
        return False
    lum = ((0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]) / 255.0)[opaque]
    bg = _lum(R.hex2rgba(bg_hex))
    invisible = float((np.abs(lum - bg) < 0.08).mean())
    white = float((lum > 0.85).mean())
    return bg < 0.5 and invisible > 0.15 and white < 0.12


def logo_display_size(path, h, max_w):
    from PIL import Image
    with Image.open(path) as im:
        s = h / im.height
        if im.width * s > max_w:
            s = max_w / im.width
        return int(im.width * s), int(im.height * s)


def fit_size(text, font, max_w, max_h, tracking=0):
    size = int(max_h)
    for _ in range(14):
        w = R.text_width(text, font, size, tracking)
        if w <= max_w:
            break
        size = int(size * max(0.6, max_w / max(1, w) * 0.98))
    return max(12, size)


def txt_item(text, t, x, y, size, font, color, **kw):
    d = {"kind": "text", "text": text, "t": r3(t), "x": r3(x), "y": r3(y), "size": int(size), "font": font, "color": color}
    d.update(kw)
    return d


def img_item(src, t, x, y, h, rot=0, anim="pop", **kw):
    d = {"kind": "image", "src": src, "t": r3(t), "x": r3(x), "y": r3(y), "h": int(h), "rot": rot, "anim": anim}
    d.update(kw)
    return d


# ----------------------------------------------------------------------------------- cards
def phrase_groups(words, max_words, max_dur=1.9):
    groups, cur = [], []
    for w in words:
        cur.append(w)
        dur = cur[-1][3] - cur[0][2]
        if len(cur) >= max_words or (len(cur) >= 2 and PUNCT_BREAK.search(w[0])) or (len(cur) >= 2 and dur > max_dur):
            groups.append(cur)
            cur = []
    if cur:
        if groups and len(cur) == 1 and len(groups[-1]) < max_words + 1 and not PUNCT_BREAK.search(groups[-1][-1][0]):
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    return groups


def pick_keyword(group, forced=None):
    if forced:
        for i, w in enumerate(group):
            if core(w[0]).lower() == core(forced).lower():
                return i
    em = [i for i, w in enumerate(group) if w[1]]
    if em:
        return max(em, key=lambda i: len(core(group[i][0])))
    cand = [i for i, w in enumerate(group) if core(w[0]).lower() not in STOP and core(w[0])] or list(range(len(group)))
    return max(cand, key=lambda i: (len(core(group[i][0])), i))


def layout_line(ctx, words, y, size, font, color, cx, max_w):
    """Words of one line centred at cx; each word is its own item so it pops in on its own timing."""
    texts = [w[0] for w in words]
    gap = size * 0.28
    widths = [R.text_width(t, font, size) for t in texts]
    total = sum(widths) + gap * (len(texts) - 1)
    if total > max_w:
        size = int(size * max_w / total)
        widths = [R.text_width(t, font, size) for t in texts]
        gap = size * 0.28
        total = sum(widths) + gap * (len(texts) - 1)
    x = cx - total / 2
    items = []
    for (t, emph, s, e), w in zip(words, widths):
        items.append(txt_item(t, s - 0.02, x + w / 2, y, size, font, color))
        x += w + gap
    return items, size


def display_case(t):
    return t.upper() if is_latin(t) else t


def card_clip(ctx, start, end, group, scene, flash=False):
    W, H, U = ctx.W, ctx.H, ctx.U
    cs = ctx.style["cards"]
    f = ctx.fonts
    col = ctx.next_card_colors()
    bgword = display_case(scene.get("bg_word") or ctx.brand.get("bg_word") or "")
    clip = {"type": "flash" if flash else "card", "start": r3(start), "end": r3(end), "bg": col["bg"],
            "bgword": bgword if cs.get("bg_word", True) and bgword else "", "bgword_color": col["word"],
            "bgword_font": f.get("bg_word", "bebas"), "bgword_size": int(0.59 * H if not ctx.vertical else 0.30 * H),
            "bgword_drift": -12 * ctx.side, "grid": cs.get("grid", False), "items": []}

    def rel(t):
        return t - start
    ink = ctx.pal.get("ink", "#141414")
    if flash:
        n = len(group)
        for i, (t, emph, s, e) in enumerate(group):
            until = group[i + 1][2] - start if i + 1 < n else None
            last = i == n - 1
            word = re.sub(r"[,;:.।]+$", "", display_case(t)) or display_case(t)
            font = f["display_bold"] if (last or emph) else f["display"]
            size = fit_size(word, font, W * 0.80, U * (0.36 if last else 0.25))
            it = txt_item(word, rel(s) - 0.02, W / 2, H * 0.48, size, font, ctx.accent() if (last or emph) else ink,
                          anim="slam" if (last or emph) else "fade")
            if until is not None:
                it["until"] = r3(until)
            clip["items"].append(it)
        stk = ctx.next_sticker() if scene.get("sticker", True) and not ctx.vertical else None
        if stk:
            clip["items"].append(img_item(stk, rel(group[-1][2]) + 0.05, W * 0.88, H * 0.74, U * 0.48, rot=-6, max_w=W * 0.24))
        clip["desc"] = "Flash words: " + " / ".join(w[0] for w in group)
        return clip
    ki = pick_keyword(group, scene.get("keyword"))
    before, kw, after = group[:ki], group[ki], group[ki + 1:]
    stk = ctx.next_sticker() if scene.get("sticker", True) else None
    ctx.side *= -1
    if ctx.vertical or ctx.square:
        cx, max_w = W / 2, W * 0.88
        stk_pos = (W * 0.5, H * (0.80 if ctx.vertical else 0.82), U * (0.40 if ctx.vertical else 0.30))
    else:
        cx = W / 2 - ctx.side * W * 0.07 if stk else W / 2
        max_w = W * (0.66 if stk else 0.84)
        stk_pos = (W / 2 + ctx.side * W * 0.385, H * 0.76, U * 0.47)
    accent = ctx.accent()
    kw_text = re.sub(r"[,.;:।]+$", "", display_case(kw[0]))
    kw_font = f["display_bold"] if kw[1] else f["display"]
    kw_size = fit_size(kw_text, kw_font, max_w, U * cs.get("keyword_height", 0.34))
    small = int(U * cs.get("small_height", 0.105))
    kw_y = H * (0.45 if ctx.vertical else 0.48 if ctx.square else 0.50)
    if before and not after:
        kw_y += U * 0.06
    if after and not before:
        kw_y -= U * 0.06
    kw_half = kw_size * 0.62
    clip["items"].append(txt_item(kw_text, rel(kw[2]) - 0.02, cx, kw_y, kw_size, kw_font, accent,
                                  anim="slam" if (kw[1] and cs.get("slam_emphasis", True)) else "fade"))
    if before:
        items, _ = layout_line(ctx, [(w[0], w[1], rel(w[2]), rel(w[3])) for w in before], kw_y - kw_half - small * 0.62,
                               small, f["body_semi"] if len(before) <= 2 else f["body"], ink, cx, max_w)
        clip["items"] += items
    if after:
        shift = W * 0.05 if not (ctx.vertical or ctx.square) and len(after) <= 2 else 0
        items, _ = layout_line(ctx, [(w[0], w[1], rel(w[2]), rel(w[3])) for w in after], kw_y + kw_half + small * 0.55,
                               small, f["body"], ink, cx + shift, max_w)
        clip["items"] += items
    if stk:
        clip["items"].append(img_item(stk, rel(kw[2]) + 0.06, stk_pos[0], stk_pos[1], stk_pos[2],
                                      rot=-5 * ctx.side, max_w=W * (0.26 if not ctx.vertical else 0.5)))
    if cs.get("bang_on_punct", True) and re.search(r"[!?]$", group[-1][0]) and not (ctx.vertical or ctx.square):
        bx = W / 2 - ctx.side * W * 0.40 if stk else W * 0.10
        clip["items"].append(txt_item("!", rel(group[-1][2]) + 0.05, bx, H * 0.44, U * 0.46, f["display_bold"],
                                      ctx.pal.get("alert", "#E0251B"), stroke=int(U * 0.013), rot=-14 * ctx.side, anim="pop"))
    clip["desc"] = f'Kinetic card: "{" ".join(w[0] for w in group)}" (keyword {kw_text})' + (" + sticker" if stk else "")
    return clip


def build_card_scene(ctx, scene, s0, s1, words, flash=False):
    cs = ctx.style["cards"]
    if not words:
        words = [(scene.get("subtitle") or scene.get("narration") or " ", True, s0 + 0.05, s1 - 0.05)]
    groups = [words] if flash else phrase_groups(words, cs.get("max_words", 5))
    for gi, g in enumerate(groups):
        a = s0 if gi == 0 else g[0][2] - 0.03
        b = s1 if gi == len(groups) - 1 else groups[gi + 1][0][2] - 0.03
        if b - a < 0.08:
            continue
        ctx.clips.append(card_clip(ctx, a, b, g, scene, flash))


def build_logo_scene(ctx, scene, s0, s1, words):
    W, H, U = ctx.W, ctx.H, ctx.U
    f = ctx.fonts
    col = ctx.next_card_colors()
    clip = {"type": "card", "start": r3(s0), "end": r3(s1), "bg": col["bg"], "bgword": "", "items": [],
            "desc": "Brand reveal: logo + name"}
    t0 = (words[0][2] if words else s0 + 0.05) - s0
    logo = ctx.brand.get("logo")
    name = ctx.brand.get("name") or ""
    if logo:
        clip["items"].append(img_item(logo, t0, W / 2, H * 0.36, U * 0.32, anim="pop", max_w=W * 0.7))
    if name:
        size = fit_size(name, f["display"], W * 0.8, U * 0.2)
        nt = next((w[2] for w in words if core(w[0]).lower() and core(w[0]).lower() in core(name).lower()), None)
        clip["items"].append(txt_item(name, (nt - s0 if nt else t0 + 0.15), W / 2, H * (0.66 if logo else 0.5), size,
                                      f["display"], ctx.accent(), anim="slam"))
    ctx.clips.append(clip)


# ----------------------------------------------------------------------------------- footage
def rec_mark(rec, label):
    for lbl, t in rec.get("marks", []):
        if lbl == label or lbl.split(":")[0] == label or label.lower() in lbl.lower():
            return float(t)
    return None


def cursor_at(rec, t):
    cur = rec.get("cursor") or []
    if not cur:
        return None
    ts = [c[0] for c in cur]
    return float(np.interp(t, ts, [c[1] for c in cur])), float(np.interp(t, ts, [c[2] for c in cur]))


def footage_camera(ctx, clip, rec, s0, s1, remap, emph_times):
    cam_st = ctx.style["camera"]
    css_w, css_h = clip["css_w"], clip["css_h"]
    panel = clip.get("layout") == "panel"
    zb, zmin, zmax = cam_st.get("zoom_base", 1.9), cam_st.get("zoom_min", 1.3), cam_st.get("zoom_max", 2.6)
    if panel:   # the panel window is already a crop of the page; zoom gently on top of it
        zb, zmin, zmax = 1.12, 1.0, 1.45
    if clip["type"] == "video":
        zb, zmin, zmax = 1.06, 1.0, 1.15
    dur = s1 - s0

    def src_of(t):
        return float(np.interp(t, [r[0] for r in remap], [r[1] for r in remap]))
    follow = cam_st.get("follow_cursor", True) and rec and rec.get("cursor")
    n = max(2, int(math.ceil(dur / 0.7)) + 1)
    pts = []
    for i in range(n):
        t = dur * i / (n - 1)
        p = cursor_at(rec, src_of(t)) if follow else None
        pts.append((t, p[0], p[1]) if p else (t, css_w / 2, css_h / 2))
    xs = np.array([p[1] for p in pts])
    ys = np.array([p[2] for p in pts])
    for arr in (xs, ys):   # forward+backward smoothing so the camera glides instead of chasing jitter
        for i in range(1, len(arr)):
            arr[i] = arr[i - 1] * 0.45 + arr[i] * 0.55
        for i in range(len(arr) - 2, -1, -1):
            arr[i] = arr[i + 1] * 0.35 + arr[i] * 0.65
    z0 = zb * (0.96 if follow else 0.93)
    keys = []
    for i, (t, _, _) in enumerate(pts):
        z = z0 + (zb - z0) * (t / max(dur, 1e-3))
        if not follow:
            xs[i] = css_w / 2 + (t / max(dur, 1e-3) - 0.5) * css_w * 0.04 * ctx.side
        keys.append([r3(t), r3(xs[i]), r3(ys[i]), round(min(zmax, max(zmin, z)), 3)])
    if cam_st.get("punch_on_emphasis", True) and emph_times:
        for et in emph_times[:3]:
            lt = et - s0
            if 0.15 < lt < dur - 0.2:
                base = R.cam_at(keys, lt)
                keys.append([r3(lt - 0.10), r3(base[0]), r3(base[1]), round(base[2], 3)])
                keys.append([r3(lt + 0.08), r3(base[0]), r3(base[1]), round(min(zmax, base[2] * 1.14), 3), "out"])
        keys.sort(key=lambda k: k[0])
        dedup = []
        for k in keys:
            if dedup and k[0] - dedup[-1][0] < 0.05:
                dedup[-1] = k
            else:
                dedup.append(k)
        keys = dedup
    return keys


def build_footage_scene(ctx, scene, s0, s1, words, seg):
    """seg: {src, css_w, css_h, a, b, rec (meta) or None, kind: web|video}"""
    cam_st = ctx.style["camera"]
    dur = s1 - s0
    a, b = seg["a"], seg["b"]
    max_sp, min_sp = cam_st.get("max_speed", 2.4), cam_st.get("min_speed", 0.45)
    if seg["kind"] == "video":
        max_sp, min_sp = 1.6, 0.6
    sp = (b - a) / max(dur, 1e-3)
    if sp > max_sp:
        b = a + max_sp * dur
    elif sp < min_sp:
        b = a + min_sp * dur
    src_len = seg.get("src_duration")
    if src_len and b > src_len:            # never run past the end of the source
        shift = b - src_len
        a, b = max(0.0, a - shift), src_len
    remap = [[0.0, r3(a)], [r3(dur), r3(b)]]
    layout = "panel" if (ctx.vertical or ctx.square) and (seg["kind"] == "web" or (seg.get("aspect", 1.78) > 1.2)) else "full"
    clip = {"type": "web" if seg["kind"] == "web" else "video", "start": r3(s0), "end": r3(s1), "src": seg["src"],
            "css_w": seg["css_w"], "css_h": seg["css_h"], "remap": remap, "layout": layout, "overlays": []}
    if seg["kind"] == "web" and cam_st.get("grade"):
        clip["grade"] = cam_st["grade"]
    if layout == "panel":
        clip["panel_y"] = 0.42 if ctx.vertical else 0.46
        if seg["kind"] == "web":
            # website text must stay legible on a phone: a squarer window that follows the cursor
            # (cropped, never stretched) instead of the whole 16:9 page shrunk into a strip
            clip["panel_w"] = 0.96 if ctx.vertical else 0.92
            clip["panel_aspect"] = 1.0 if ctx.vertical else 0.75
        else:
            clip["panel_w"] = 0.96 if ctx.vertical else 0.90
            clip["panel_aspect"] = seg["css_h"] / max(1, seg["css_w"])
    emph = [w[2] for w in words if w[1]]
    rec = seg.get("rec")
    if rec:
        for c in rec.get("clicks", []):
            if a <= c[0] <= b:
                emph.append(s0 + (c[0] - a) / max(1e-3, (b - a)) * dur)
    clip["cam"] = footage_camera(ctx, clip, rec, s0, s1, remap, sorted(emph))
    stk = scene.get("sticker_overlay")
    if stk is True:                      # the planner only flags the scene; pick the next available cut-out
        stk = ctx.next_sticker()
    if isinstance(stk, str) and stk and not ctx.vertical:
        clip["overlays"].append(img_item(stk, 0.25, ctx.W * 0.9, ctx.H * 0.66, ctx.U * 0.40, rot=-4, max_w=ctx.W * 0.2))
    label = seg.get("label") or os.path.basename(seg["src"])
    clip["desc"] = (f"{'Website recording' if seg['kind'] == 'web' else 'Video'}: {label} "
                    f"(source {a:.1f}-{b:.1f}s, {((b - a) / max(dur, 1e-3)):.2f}x)")
    ctx.clips.append(clip)


def build_still_scene(ctx, scene, s0, s1, asset, idx):
    W, H = ctx.W, ctx.H
    ar = (asset.get("w") or 1) / max(1, asset.get("h") or 1)
    fit = "cover" if abs(ar - W / H) / (W / H) < 0.18 and not asset.get("alpha") else "contain"
    zin = idx % 2 == 0
    clip = {"type": "still", "start": r3(s0), "end": r3(s1), "src": asset["path"], "fit": fit,
            "z0": 1.0 if zin else 1.09, "z1": 1.09 if zin else 1.0, "pan": [0.015 * ctx.side, 0],
            "desc": f"Image: {asset.get('name', os.path.basename(asset['path']))} (Ken Burns {'in' if zin else 'out'})"}
    if asset.get("alpha"):
        clip["bg"] = ctx.next_card_colors()["bg"]
    ctx.clips.append(clip)


def allocate_segments(ctx, scenes, times):
    """Which part of which recording / video each footage scene shows."""
    recs = ctx.plan.get("recordings") or {}
    assets = ctx.plan.get("assets") or {}
    out, by_rec = {}, {}
    for sc in scenes:
        v = sc.get("visual") or {}
        if v.get("type") == "web":
            name = v.get("recording") if v.get("recording") in recs else next(iter(recs), None)
            if name:
                by_rec.setdefault(name, []).append(sc)
    for name, scs in by_rec.items():
        rec = recs[name]
        dur = rec.get("duration", 10.0)
        lo = max(0.0, rec.get("usable_start", 0.0))
        hi = min(dur, rec.get("usable_end", dur))
        auto = []
        for sc in scs:
            v = sc["visual"]
            a = v.get("from_t")
            if a is None and v.get("from"):
                a = rec_mark(rec, v["from"])
            if a is not None:
                nxt = sorted(t for _, t in rec.get("marks", []) if t > a + 0.5)
                out[sc["id"]] = {"a": a, "b": nxt[0] if nxt else min(hi, a + 4)}
            else:
                auto.append(sc)
        if auto:
            durs = np.array([max(0.3, times[s["id"]][1] - times[s["id"]][0]) for s in auto])
            edges = lo + np.concatenate([[0], np.cumsum(durs / durs.sum())]) * (hi - lo)
            for sc, x, y in zip(auto, edges[:-1], edges[1:]):
                out[sc["id"]] = {"a": float(x), "b": float(y)}
        for sc in scs:
            if sc["id"] in out:
                out[sc["id"]].update({"src": rec["src"], "css_w": rec.get("css_w", 1920), "css_h": rec.get("css_h", 1080),
                                      "rec": rec, "kind": "web", "label": rec.get("title", name), "src_duration": dur})
    vid_cursor = {}
    for sc in scenes:
        v = sc.get("visual") or {}
        if v.get("type") == "video" and v.get("asset_id") in assets:
            a = assets[v["asset_id"]]
            d = times[sc["id"]][1] - times[sc["id"]][0]
            lead = float(a.get("usable_start") or 0.0)
            start = vid_cursor.get(a["id"], lead)
            total = a.get("duration") or 5.0
            if start + d > total:
                start = lead
            vid_cursor[a["id"]] = start + d
            out[sc["id"]] = {"src": a["proxy"] if a.get("proxy") else a["path"], "css_w": a.get("w") or 1920,
                             "css_h": a.get("h") or 1080, "a": start, "b": min(total, start + d), "rec": None, "kind": "video",
                             "label": a.get("name"), "src_duration": total, "aspect": (a.get("w") or 16) / max(1, a.get("h") or 9)}
    return out


# ----------------------------------------------------------------------------------- subtitles
def add_subtitles(ctx, words, s_end, max_words=4, min_words=2, s_start=0.0):
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or (len(cur) >= min_words and PUNCT_BREAK.search(w[0])):
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and len(cur) < min_words and len(chunks[-1]) + len(cur) <= max_words + 1:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    for i, ch in enumerate(chunks):
        t0 = max(ch[0][2] - 0.06, s_start, ctx.subs[-1]["end"] if ctx.subs else 0.0)
        t1 = (chunks[i + 1][0][2] - 0.06) if i + 1 < len(chunks) else s_end - 0.02
        if t1 - t0 < 0.1:
            continue
        ctx.subs.append({"start": r3(t0), "end": r3(t1), "words": [[w[0], bool(w[1]), r3(w[2]), r3(w[3])] for w in ch]})


# ----------------------------------------------------------------------------------- main build
def build(plan, timing):
    """plan: {output:{w,h,fps}, style, scenes, recordings, assets, stickers, fonts, brand, root}
    timing: {scene_id: {start, end, words:[{w,s,e}]}} -> EDL dict"""
    ctx = Ctx(plan)
    st = ctx.style
    scenes = sorted([s for s in plan["scenes"] if s["id"] in timing], key=lambda s: timing[s["id"]]["start"])
    if not scenes:
        raise ValueError("no timed scenes")
    times = {}
    for i, sc in enumerate(scenes):
        a = 0.0 if i == 0 else timing[sc["id"]]["start"] - 0.03
        b = timing[scenes[i + 1]["id"]]["start"] - 0.03 if i + 1 < len(scenes) else timing[sc["id"]]["end"] + 0.12
        times[sc["id"]] = (a, b)
    voice_end = max(t["end"] for t in timing.values())
    segs = allocate_segments(ctx, scenes, times)
    assets = plan.get("assets") or {}
    sub_cfg = st["subtitles"]
    still_i = 0
    for i, sc in enumerate(scenes):
        s0, s1 = times[sc["id"]]
        if i == len(scenes) - 1:
            s1 = voice_end + (st["structure"].get("sting_pad", 0.34) if st["structure"].get("end_card", True) else 0.6)
        tm = timing[sc["id"]]
        display = tokens_with_emph(sc.get("subtitle") or sc.get("narration", ""))
        words = map_display_words(display, tm.get("words") or [], tm["start"], tm["end"])
        v = (sc.get("visual") or {}).get("type", "card")
        n_before = len(ctx.clips)
        if v in ("web", "video") and sc["id"] in segs:
            build_footage_scene(ctx, sc, s0, s1, words, segs[sc["id"]])
        elif v == "image" and sc["visual"].get("asset_id") in assets:
            build_still_scene(ctx, sc, s0, s1, assets[sc["visual"]["asset_id"]], still_i)
            still_i += 1
        elif v == "logo" and (ctx.brand.get("logo") or ctx.brand.get("name")):
            build_logo_scene(ctx, sc, s0, s1, words)
        elif v == "flash":
            build_card_scene(ctx, sc, s0, s1, words, flash=True)
        else:
            build_card_scene(ctx, sc, s0, s1, words, flash=False)
        new = ctx.clips[n_before:]
        for c in new:
            c["scene"] = sc["id"]
        is_footage = any(c["type"] in ("web", "video", "still") for c in new)
        if words and (is_footage or sub_cfg.get("on_cards", False)) and sub_cfg.get("enabled", True):
            add_subtitles(ctx, words, s1, sub_cfg.get("max_words", 4), sub_cfg.get("min_words", 2), s0)
        if sub_cfg.get("on_cards", False):
            for c in new:
                c["subs"] = True
    end_start = ctx.clips[-1]["end"]
    duration = end_start
    if st["structure"].get("end_card", True):
        hold = st["structure"].get("end_hold", 3.6)
        W, H, U = ctx.W, ctx.H, ctx.U
        f, pal = ctx.fonts, ctx.pal
        items = []
        logo = ctx.brand.get("logo")
        ly = H * (0.38 if logo else 0.45)
        plate = None
        if logo:
            items.append({"kind": "image", "src": logo, "h": int(U * 0.31), "max_w": int(W * 0.7), "x": W / 2, "y": ly, "t": 0.0, "anim": "pop"})
            lw, lh = logo_display_size(logo, int(U * 0.31), int(W * 0.7))
            if needs_logo_plate(logo, pal.get("end_bg", "#140C1E")):
                plate = {"w": lw + U * 0.12, "h": lh + U * 0.10, "radius": int(U * 0.04), "color": "#FFFFFF"}
        name = display_case(ctx.brand.get("handle") or ctx.brand.get("name") or "")
        if name:
            items.append({"kind": "text", "text": name, "font": f.get("brand", "bebas"), "color": "#FFFFFF", "tracking": 3,
                          "size": fit_size(name, f.get("brand", "bebas"), W * 0.84, U * 0.096, 3),
                          "x": W / 2, "y": H * (0.65 if logo else 0.45), "t": 0.22, "rise": 26})
        tag = display_case(ctx.brand.get("tagline") or "")
        if tag:
            items.append({"kind": "text", "text": tag, "font": f.get("display_med", "oswald-med"), "color": pal.get("tagline", "#FF4D8D"),
                          "tracking": 6, "size": fit_size(tag, f.get("display_med", "oswald-med"), W * 0.84, U * 0.043, 6),
                          "x": W / 2, "y": H * (0.73 if logo else 0.56), "t": 0.45, "rise": 18})
        ctx.clips.append({"type": "endcard", "start": r3(end_start), "end": r3(end_start + hold), "bg": pal.get("end_bg", "#140C1E"),
                          "bgword": display_case(ctx.brand.get("bg_word") or ""), "bgword_color": pal.get("end_word", "#1E142B"),
                          "logo_y": ly, "glow_t": 0.0, "glow_color": pal.get("glow"), "logo_plate": plate,
                          "slow_zoom": st.get("endcard", {}).get("slow_zoom", 0.035),
                          "items": items, "scene": "END", "desc": "End card: logo + brand + tagline (lands on the music sting)"})
        duration = end_start + hold
    tr = st["transitions"]
    for x, y in zip(ctx.clips[:-1], ctx.clips[1:]):
        kx = "web" if x["type"] in ("web", "video", "still") else "card"
        ky = "web" if y["type"] in ("web", "video", "still") else "card"
        if y["type"] == "endcard":
            kind = tr.get("to_end", "cut")
        else:
            kind = tr.get(f"{kx}_{ky}", "cut")
        if kind == "cut" or (x.get("scene") == y.get("scene") and kx == "card" and ky == "card"):
            continue
        t = {"at": y["start"], "kind": kind, "dur": tr.get("fade_duration", 0.4) if kind == "fade" else tr.get("duration", 0.22)}
        if kind == "whip":
            t["dir"] = ctx.whip_dir
            if tr.get("alternate", True):
                ctx.whip_dir = "down" if ctx.whip_dir == "up" else "up"
        ctx.transitions.append(t)
    wm_cfg = st.get("watermark", {})
    wm = None
    if wm_cfg.get("enabled", True) and (ctx.brand.get("logo_icon") or ctx.brand.get("logo") or ctx.brand.get("handle")):
        # with a logo, the text next to it is the handle / domain only (the logo already shows the name)
        wm_text = ctx.brand.get("handle") or ("" if (ctx.brand.get("logo_icon") or ctx.brand.get("logo")) else ctx.brand.get("name") or "")
        wm = {"icon": ctx.brand.get("logo_icon") or ctx.brand.get("logo"),
              "text": display_case(wm_text),
              "icon_h": int(ctx.U * 0.067), "font": ctx.fonts.get("brand", "bebas")}
        for c in ctx.clips:
            if c["type"] in ("card", "flash"):
                c["watermark"] = wm_cfg.get("card", "card") if not (ctx.vertical or ctx.square) else "corner"
            elif c["type"] in ("web", "video", "still"):
                c["watermark"] = wm_cfg.get("web", "corner")
    ss = {"enabled": sub_cfg.get("enabled", True),
          "size": int(ctx.U * sub_cfg.get("size", 0.0556) * (1.15 if ctx.vertical else 1.0)),
          "y": ctx.H * sub_cfg.get("y", 0.896), "font": ctx.fonts.get("subtitle", "inter-semi"),
          "emph_font": ctx.fonts.get("subtitle_emph", "inter-italic"), "color": sub_cfg.get("color", "#FFFFFF"),
          "emph_color": sub_cfg.get("emph_color", sub_cfg.get("color", "#FFFFFF")), "shadow": sub_cfg.get("shadow", 0.62),
          "emph_glow": sub_cfg.get("emph_glow", 0.55), "box": sub_cfg.get("box"),
          "karaoke": sub_cfg.get("karaoke", False), "highlight_color": sub_cfg.get("highlight_color", ctx.pal["accents"][0]),
          "max_width": 0.86}
    if ctx.vertical:
        ss["y"] = ctx.H * 0.70
    elif ctx.square:
        ss["y"] = ctx.H * 0.88
    edl = {"root": plan.get("root", os.getcwd()), "fps": ctx.FPS, "w": ctx.W, "h": ctx.H, "duration": r3(duration),
           "voice_end": r3(voice_end), "sting_at": r3(end_start), "fade_out": st.get("output", {}).get("fade_out", 0.45),
           "watermark": wm, "subtitle_style": ss, "fonts": plan.get("fonts") or {},
           "clips": ctx.clips, "transitions": ctx.transitions, "subtitles": ctx.subs}
    for x, y in zip(ctx.clips[:-1], ctx.clips[1:]):
        if abs(x["end"] - y["start"]) > 0.002:
            raise ValueError(f"non-contiguous clips: {x['type']} ends {x['end']} / {y['type']} starts {y['start']}")
    return edl
