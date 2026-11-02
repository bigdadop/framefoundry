"""Frame compositor: edit decision list (EDL) -> RGB frames.

Generalised from the SunoSunao promo renderer. Clip types:
  card     kinetic typography: flat background, giant faded topic word, items popping in on cue
  flash    one big word at a time
  web      real website recording with an eased virtual camera (CSS-pixel keyframes) + motion blur
  video    any uploaded clip (same camera model, gentle moves)
  still    image with a Ken-Burns move (never distorted: contain-fit over a blurred fill)
  endcard  dark card with logo glow, brand name and tagline
Transitions: cut | whip (vertical slide + motion blur) | punch (zoom blur) | fade (crossfade).
Text is shaped with libraqm when Pillow has it, so Devanagari conjuncts and matras render correctly;
Latin-only fonts are swapped automatically for Devanagari-capable ones (Teko / Poppins).
All positions are output pixels; web camera keyframes are in the recording's CSS pixels.
"""
import functools
import math
import os
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from framefoundry.util import ROOT

W, H, FPS = 1920, 1080, 30
ASSET_ROOT = os.getcwd()
FONT_DIR = str(ROOT / "assets" / "fonts")
LAYOUT = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# name -> (file, {axis: value})
FONTS = {
    "oswald": ("Oswald-Variable.ttf", {"wght": 600}),
    "oswald-bold": ("Oswald-Variable.ttf", {"wght": 700}),
    "oswald-med": ("Oswald-Variable.ttf", {"wght": 500}),
    "bebas": ("BebasNeue-Regular.ttf", {}),
    "inter": ("Inter-Variable.ttf", {"opsz": 32, "wght": 500}),
    "inter-semi": ("Inter-Variable.ttf", {"opsz": 32, "wght": 600}),
    "inter-bold": ("Inter-Variable.ttf", {"opsz": 32, "wght": 700}),
    "inter-italic": ("Inter-Italic-Variable.ttf", {"opsz": 32, "wght": 600}),
    "poppins-med": ("Poppins-Medium.ttf", {}),
    "poppins-semi": ("Poppins-SemiBold.ttf", {}),
    "poppins-bold": ("Poppins-Bold.ttf", {}),
    "sora": ("Sora-Variable.ttf", {"wght": 700}),
    "teko": ("Teko-Variable.ttf", {"wght": 600}),
    "teko-bold": ("Teko-Variable.ttf", {"wght": 700}),
}
# Latin-only fonts -> Devanagari-capable counterparts (used automatically for Hindi text)
DEVA_FALLBACK = {"oswald": "teko", "oswald-bold": "teko-bold", "oswald-med": "teko", "bebas": "teko-bold",
                 "inter": "poppins-med", "inter-semi": "poppins-semi", "inter-bold": "poppins-bold",
                 "inter-italic": "poppins-semi", "sora": "poppins-bold"}


def configure(edl):
    """Set output geometry, asset root and project fonts from an EDL."""
    global W, H, FPS, ASSET_ROOT
    W, H, FPS = int(edl.get("w", 1920)), int(edl.get("h", 1080)), int(edl.get("fps", 30))
    ASSET_ROOT = edl.get("root") or os.getcwd()
    for name, path in (edl.get("fonts") or {}).items():
        FONTS[name] = (path, {})
    _still_bg.cache_clear()


# ------------------------------------------------------------------------------------------ easing
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def ease_io(t):
    t = clamp(t)
    return 4 * t * t * t if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in(t):
    t = clamp(t)
    return t * t * t


def ease_back(t, s=1.70158):
    t = clamp(t) - 1
    return t * t * ((s + 1) * t + s) + 1


EASE = {"io": ease_io, "out": ease_out, "in": ease_in, "linear": clamp, "back": ease_back}


# ------------------------------------------------------------------------------------------ text
def font_for(name, text):
    if DEVANAGARI.search(text or ""):
        return DEVA_FALLBACK.get(name, name)
    return name


@functools.lru_cache(maxsize=256)
def get_font(name, size):
    f, axes = FONTS.get(name, FONTS["inter"])
    path = f if os.path.isabs(f) else os.path.join(FONT_DIR, f)
    font = ImageFont.truetype(path, max(1, int(size)), layout_engine=LAYOUT)
    if axes:
        try:
            tags = {"Weight": "wght", "Optical size": "opsz", "Optical Size": "opsz"}
            vals = []
            for a in font.get_variation_axes():
                n = a.get("name")
                n = n.decode() if isinstance(n, bytes) else n
                vals.append(axes.get(tags.get(n, n), a.get("default")))
            font.set_variation_by_axes(vals)
        except (OSError, AttributeError):
            pass
    return font


def text_width(text, font, size, tracking=0):
    font = font_for(font, text)
    f = get_font(font, size)
    if tracking and not DEVANAGARI.search(text):
        return sum(f.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)
    return f.getlength(text)


def hex2rgba(c, a=255):
    c = (c or "#000000").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


@functools.lru_cache(maxsize=4096)
def text_layer(text, font, size, color, tracking=0, shadow=0.0, glow=0.0, glow_color="#FFFFFF",
               stroke=0, stroke_color="#000000"):
    """Text -> tight RGBA image with optional soft shadow / glow / stroke. Returns (img, pad, ascent)."""
    font = font_for(font, text)
    f = get_font(font, size)
    complex_script = bool(DEVANAGARI.search(text))
    if complex_script:
        tracking = 0                                   # per-glyph drawing would break conjunct shaping
    asc, desc = f.getmetrics()
    if tracking == 0:
        x0, _, x1, _ = f.getbbox(text)
        tw = int(max(f.getlength(text), x1) - min(0, x0)) + 2 * stroke + 2
        widths = None
    else:
        widths = [f.getlength(ch) for ch in text]
        tw = int(sum(widths) + tracking * max(0, len(text) - 1)) + 2 * stroke + 2
    pad = int(size * 0.45) if (shadow or glow) else int(size * 0.12) + stroke
    extra = int(size * 0.25) if complex_script else 0     # room for matras above / below
    img = Image.new("RGBA", (tw + 2 * pad, asc + desc + 2 * pad + 2 * extra), (0, 0, 0, 0))

    def draw_text(dst, fill, xoff=0, yoff=0, sw=0, sc=None):
        d = ImageDraw.Draw(dst)
        x = pad + xoff + stroke
        if widths is None:
            d.text((x, pad + extra + yoff), text, font=f, fill=fill, stroke_width=sw, stroke_fill=sc)
        else:
            for ch, w in zip(text, widths):
                d.text((x, pad + extra + yoff), ch, font=f, fill=fill, stroke_width=sw, stroke_fill=sc)
                x += w + tracking
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_text(sh, (0, 0, 0, int(255 * shadow)), 0, int(size * 0.05))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(size * 0.12)))
    if glow:
        gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_text(gl, hex2rgba(glow_color, int(255 * glow)))
        img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(size * 0.16)))
    fg = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_text(fg, hex2rgba(color), sw=stroke, sc=hex2rgba(stroke_color) if stroke else None)
    img.alpha_composite(fg)
    return img, pad, asc


def _abspath(p):
    return p if os.path.isabs(p) else os.path.join(ASSET_ROOT, p)


@functools.lru_cache(maxsize=64)
def load_png(path):
    return Image.open(_abspath(path)).convert("RGBA")


@functools.lru_cache(maxsize=256)
def scaled_png(path, h, rot=0.0, max_w=0):
    """Scale an image to height h (or to max_w width if that is smaller) - aspect ratio is always kept."""
    im = load_png(path)
    s = h / im.height
    if max_w and im.width * s > max_w:
        s = max_w / im.width
    im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    if rot:
        im = im.rotate(rot, resample=Image.BICUBIC, expand=True)
    return im


def paste_center(dst, im, cx, cy, alpha=1.0, scale=1.0):
    if alpha <= 0.003:
        return
    if abs(scale - 1) > 1e-3:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BICUBIC)
    if alpha < 0.999:
        a = im.getchannel("A").point(lambda v: int(v * alpha))
        im = im.copy()
        im.putalpha(a)
    x, y = int(round(cx - im.width / 2)), int(round(cy - im.height / 2))
    if x >= 0 and y >= 0 and x + im.width <= dst.width and y + im.height <= dst.height:
        dst.alpha_composite(im, (x, y))
    else:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(dst.width, x + im.width), min(dst.height, y + im.height)
        if x1 > x0 and y1 > y0:
            dst.alpha_composite(im.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


# ------------------------------------------------------------------------------------------ cards
def item_image(it):
    if it["kind"] == "text":
        img, _, _ = text_layer(it["text"], it.get("font", "inter"), int(it["size"]), it.get("color", "#141414"),
                               int(it.get("tracking", 0)), float(it.get("shadow", 0)), float(it.get("glow", 0)),
                               it.get("glow_color", "#FFFFFF"), int(it.get("stroke", 0)), it.get("stroke_color", "#FFFFFF"))
        if it.get("rot"):
            img = img.rotate(it["rot"], resample=Image.BICUBIC, expand=True)
        return img
    if it["kind"] == "image":
        return scaled_png(it["src"], int(it["h"]), float(it.get("rot", 0)), int(it.get("max_w", 0)))
    raise ValueError(it["kind"])


def _item_anim(it, t):
    t_in = it.get("t", 0.0)
    alpha = clamp((t - t_in) / it.get("fade", 0.10))
    anim = it.get("anim", "fade")
    if anim == "pop":
        scale = 0.55 + 0.45 * ease_back(clamp((t - t_in) / 0.24), 2.2)
    elif anim == "slam":
        scale = 1.0 + 0.35 * (1 - ease_out(clamp((t - t_in) / 0.16)))
    elif anim == "none":
        scale, alpha = 1.0, 1.0
    else:
        scale = 0.965 + 0.035 * ease_out(clamp((t - t_in) / 0.18))
    return alpha, scale


def _item_offset(it, t):
    """Slide-in offsets (rise from below / slide from the side)."""
    dt = t - it.get("t", 0.0)
    dx = dy = 0.0
    if it.get("rise"):
        dy = (1 - ease_out(clamp(dt / 0.35))) * it["rise"]
    if it.get("slide"):
        dx = (1 - ease_out(clamp(dt / 0.3))) * it["slide"]
    return dx, dy


def render_card(clip, t):
    frame = Image.new("RGBA", (W, H), hex2rgba(clip.get("bg", "#FFF3DB")))
    if clip.get("grid"):
        d = ImageDraw.Draw(frame)
        step = max(20, int(min(W, H) / 12))
        col = hex2rgba(clip.get("grid_color", "#00000010"[:7]), 22)
        for x in range(0, W, step):
            d.line([(x, 0), (x, H)], fill=col, width=1)
        for y in range(0, H, step):
            d.line([(0, y), (W, y)], fill=col, width=1)
    if clip.get("bgword"):
        bw, _, _ = text_layer(clip["bgword"], clip.get("bgword_font", "bebas"), int(clip.get("bgword_size", 0.59 * H)),
                              clip.get("bgword_color", "#FFE1C3"), int(clip.get("bgword_tracking", 8)))
        if bw.width > W * 1.05:
            bw = bw.resize((int(W * 1.05), max(1, int(bw.height * W * 1.05 / bw.width))), Image.LANCZOS)
        drift = clip.get("bgword_drift", 0.0) * t
        paste_center(frame, bw, W / 2 + clip.get("bgword_dx", 0) + drift, H / 2 + clip.get("bgword_dy", 0.01 * H))
    for it in clip.get("items", []):
        if t < it.get("t", 0.0) or t >= it.get("until", 1e9):
            continue
        alpha, scale = _item_anim(it, t)
        if "until" in it and it.get("fade_out"):
            alpha *= clamp((it["until"] - t) / 0.08)
        cx, cy = it["x"], it["y"]
        dx, dy = _item_offset(it, t)
        if it.get("drift"):
            cx += it["drift"][0] * (t - it.get("t", 0))
            cy += it["drift"][1] * (t - it.get("t", 0))
        paste_center(frame, item_image(it), cx + dx, cy + dy, alpha, scale)
    return frame


def render_endcard(clip, t):
    frame = Image.new("RGBA", (W, H), hex2rgba(clip.get("bg", "#140C1E")))
    if clip.get("bgword"):
        bw, _, _ = text_layer(clip["bgword"], clip.get("bgword_font", "bebas"), int(clip.get("bgword_size", 0.59 * H)),
                              clip.get("bgword_color", "#1E142B"), 8)
        if bw.width > W * 1.05:
            bw = bw.resize((int(W * 1.05), max(1, int(bw.height * W * 1.05 / bw.width))), Image.LANCZOS)
        paste_center(frame, bw, W / 2, H / 2 + 0.01 * H)
    plate = clip.get("logo_plate")
    if plate and t >= clip.get("glow_t", 0.0):
        k = ease_out(clamp((t - clip.get("glow_t", 0.0)) / 0.35))
        pw_, ph_ = int(plate["w"]), int(plate["h"])
        sh = Image.new("RGBA", (pw_ + 120, ph_ + 120), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([60, 70, pw_ + 60, ph_ + 70], int(plate.get("radius", 40)), fill=(0, 0, 0, 120))
        paste_center(frame, sh.filter(ImageFilter.GaussianBlur(28)), W / 2, clip.get("logo_y", 0.38 * H), k)
        pl = Image.new("RGBA", (pw_, ph_), (0, 0, 0, 0))
        ImageDraw.Draw(pl).rounded_rectangle([0, 0, pw_ - 1, ph_ - 1], int(plate.get("radius", 40)), fill=hex2rgba(plate.get("color", "#FFFFFF"), 245))
        paste_center(frame, pl, W / 2, clip.get("logo_y", 0.38 * H), k, 0.96 + 0.04 * k)
    g = clip.get("glow_t", 0.0)
    if t >= g and clip.get("glow_color") and not plate:
        k = ease_out((t - g) / 0.6)
        glow = _radial_glow(int(min(W, H) * 0.52 * (0.7 + 0.3 * k)), clip["glow_color"])
        paste_center(frame, glow, W / 2, clip.get("logo_y", 0.38 * H), 0.55 * k)
    for it in clip.get("items", []):
        if t < it.get("t", 0):
            continue
        dt = t - it.get("t", 0)
        if it.get("anim") == "pop":
            scale = 0.4 + 0.6 * ease_back(clamp(dt / 0.38), 1.9)
            alpha = clamp(dt / 0.12)
        else:
            scale = 0.96 + 0.04 * ease_out(clamp(dt / 0.3))
            alpha = clamp(dt / 0.22)
        paste_center(frame, item_image(it), it["x"], it["y"] - (1 - ease_out(clamp(dt / 0.35))) * it.get("rise", 0), alpha, scale)
    z = 1.0 + clip.get("slow_zoom", 0.035) * ease_io(t / max(1e-3, clip["end"] - clip["start"]))
    if z > 1.0005:      # gentle push-in over the hold keeps the end card alive
        w2, h2 = int(W * z), int(H * z)
        big = frame.resize((w2, h2), Image.BICUBIC)
        frame = big.crop(((w2 - W) // 2, (h2 - H) // 2, (w2 - W) // 2 + W, (h2 - H) // 2 + H))
    return frame


@functools.lru_cache(maxsize=8)
def _radial_glow(d, color):
    d = max(4, d)
    yy, xx = np.mgrid[-1:1:complex(d), -1:1:complex(d)]
    a = np.clip(1 - np.sqrt(xx ** 2 + yy ** 2), 0, 1) ** 2.2
    r, g, b, _ = hex2rgba(color)
    arr = np.dstack([np.full_like(a, r), np.full_like(a, g), np.full_like(a, b), a * 255]).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


# ------------------------------------------------------------------------------------------ footage
class VideoSource:
    """Frame access without decoding everything into RAM: sequential reads, seeks only on jumps,
    small LRU cache. Short-GOP proxies make seeks fast and exact."""
    cache = {}

    @classmethod
    def get(cls, path):
        if path not in cls.cache:
            cls.cache[path] = cls(path)
        return cls.cache[path]

    def __init__(self, path):
        from collections import OrderedDict
        p = _abspath(path)
        self.cap = cv2.VideoCapture(p)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open {p}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.n = max(1, int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.pos = 0
        self.lru = OrderedDict()
        self.max_cache = 24

    def _read_at(self, i):
        if i < self.pos or i - self.pos > 45:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            self.pos = i
        frame = None
        while self.pos <= i:
            ok, f = self.cap.read()
            if not ok:
                break
            self.pos += 1
            frame = f
        return frame

    def at(self, t):
        i = min(max(int(round(t * self.fps)), 0), self.n - 1)
        if i in self.lru:
            self.lru.move_to_end(i)
            return self.lru[i]
        f = self._read_at(i)
        if f is None:
            f = next(reversed(self.lru.values())) if self.lru else np.zeros((self.h, self.w, 3), np.uint8)
        self.lru[i] = f
        if len(self.lru) > self.max_cache:
            self.lru.popitem(last=False)
        return f


def cam_at(cam, t):
    """cam: [[t, cx, cy, zoom, (ease)], ...] -> interpolated (cx, cy, z); zoom is interpolated geometrically."""
    if not cam:
        return (None, None, 1.0)
    if t <= cam[0][0]:
        return tuple(cam[0][1:4])
    for a, b in zip(cam[:-1], cam[1:]):
        if a[0] <= t <= b[0]:
            e = EASE[b[4] if len(b) > 4 else "io"]((t - a[0]) / max(1e-6, b[0] - a[0]))
            z = a[3] * (b[3] / a[3]) ** e
            return (a[1] + (b[1] - a[1]) * e, a[2] + (b[2] - a[2]) * e, z)
    return tuple(cam[-1][1:4])


def _src_time(clip, tt):
    if clip.get("remap"):
        rm = clip["remap"]
        return float(np.interp(tt, [r[0] for r in rm], [r[1] for r in rm]))
    return clip.get("src_in", 0.0) + tt * clip.get("speed", 1.0)


def _footage_patch(clip, tt, out_w, out_h):
    """Camera crop of the source at clip time tt, resized to out_w x out_h (BGR). Never distorts:
    the crop window always has the output's aspect ratio."""
    src = VideoSource.get(clip["src"])
    f = src.at(_src_time(clip, tt))
    css_w = float(clip.get("css_w", src.w))
    css_h = float(clip.get("css_h", src.h))
    s = src.w / css_w
    cx, cy, z = cam_at(clip.get("cam"), tt)
    if cx is None:
        cx, cy = css_w / 2, css_h / 2
    aspect = out_w / out_h
    if css_w / css_h > aspect:
        ch = css_h / z
        cw = ch * aspect
    else:
        cw = css_w / z
        ch = cw / aspect
    cx = min(max(cx, cw / 2), css_w - cw / 2)
    cy = min(max(cy, ch / 2), css_h - ch / 2)
    pw, ph = max(2, int(round(cw * s))), max(2, int(round(ch * s)))
    patch = cv2.getRectSubPix(f, (pw, ph), (cx * s, cy * s))
    interp = cv2.INTER_AREA if pw > out_w else cv2.INTER_CUBIC
    return cv2.resize(patch, (out_w, out_h), interpolation=interp)


@functools.lru_cache(maxsize=4)
def _round_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


def grade(img, contrast=1.0, sat=1.0, lift=0):
    f = img.astype(np.float32)
    if sat != 1.0:
        g = f.mean(2, keepdims=True)
        f = g + (f - g) * sat
    if contrast != 1.0:
        f = (f - 128) * contrast + 128 + lift
    return np.clip(f, 0, 255).astype(np.uint8)


def frame_footage(clip, t, sub_samples=1):
    """web / video clips. 'full' fills the frame; 'panel' (vertical / square outputs) shows the footage
    in a rounded panel over a blurred, darkened copy so website content is never cropped away."""
    layout = clip.get("layout", "full")
    if layout == "panel":
        pw = int(W * clip.get("panel_w", 0.94)) // 2 * 2
        ph = int(pw * clip.get("panel_aspect", 9 / 16)) // 2 * 2
        ph = min(ph, int(H * 0.8) // 2 * 2)
    else:
        pw, ph = W, H

    def one(tt):
        return _footage_patch(clip, tt, pw, ph)
    if sub_samples <= 1:
        img = one(t)
    else:
        acc = np.zeros((ph, pw, 3), np.float32)
        for k in range(sub_samples):
            acc += one(t + (k / sub_samples - 0.5) * (0.5 / FPS))
        img = (acc / sub_samples).astype(np.uint8)
    if clip.get("grade"):
        img = grade(img, **clip["grade"])
    fg = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
    if layout != "panel":
        return fg
    if clip.get("panel_bg"):
        frame = Image.new("RGBA", (W, H), hex2rgba(clip["panel_bg"]))
    else:
        small = cv2.resize(img, (max(2, pw // 10), max(2, ph // 10)), interpolation=cv2.INTER_AREA)
        sc = max(W / small.shape[1], H / small.shape[0])
        cover = cv2.resize(small, (int(small.shape[1] * sc) + 2, int(small.shape[0] * sc) + 2), interpolation=cv2.INTER_LINEAR)
        y0 = (cover.shape[0] - H) // 2
        x0 = (cover.shape[1] - W) // 2
        bg = cv2.GaussianBlur(cover[y0:y0 + H, x0:x0 + W], (0, 0), 24)
        bg = (bg.astype(np.float32) * 0.42).astype(np.uint8)
        frame = Image.fromarray(cv2.cvtColor(bg, cv2.COLOR_BGR2RGB)).convert("RGBA")
    r = int(min(pw, ph) * 0.035)
    shadow = Image.new("RGBA", (pw + 80, ph + 80), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([40, 48, pw + 40, ph + 48], r, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    py = int(H * clip.get("panel_y", 0.46))
    paste_center(frame, shadow, W / 2, py)
    fg.putalpha(_round_mask(pw, ph, r))
    paste_center(frame, fg, W / 2, py)
    return frame


def render_still(clip, t):
    """Ken Burns on an image: contain-fit over a blurred cover background (logos / products never distorted)."""
    im = load_png(clip["src"])
    d = max(1e-3, clip["end"] - clip["start"])
    k = ease_io(t / d)
    z = clip.get("z0", 1.0) + (clip.get("z1", 1.08) - clip.get("z0", 1.0)) * k
    fit = clip.get("fit", "contain")
    frame = _still_bg(clip["src"], clip.get("bg")).copy()
    s_cover = max(W / im.width, H / im.height)
    s_contain = min(W / im.width, H / im.height) * clip.get("margin", 0.86)
    s = (s_cover if fit == "cover" else s_contain) * z
    w2, h2 = max(1, int(im.width * s)), max(1, int(im.height * s))
    big = im.resize((w2, h2), Image.BICUBIC)
    dx = clip.get("pan", [0, 0])[0] * k * W
    dy = clip.get("pan", [0, 0])[1] * k * H
    if fit != "cover":
        sh = Image.new("RGBA", (w2 + 60, h2 + 60), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle([30, 36, w2 + 30, h2 + 36], fill=(0, 0, 0, 110))
        paste_center(frame, sh.filter(ImageFilter.GaussianBlur(18)), W / 2 + dx, H / 2 + dy)
    paste_center(frame, big, W / 2 + dx, H / 2 + dy)
    return frame


@functools.lru_cache(maxsize=16)
def _still_bg(src, color):
    if color:
        return Image.new("RGBA", (W, H), hex2rgba(color))
    im = load_png(src).convert("RGB")
    s = max(W / im.width, H / im.height) * 1.1
    b = im.resize((max(1, int(im.width * s / 8)), max(1, int(im.height * s / 8))), Image.BILINEAR)
    b = b.filter(ImageFilter.GaussianBlur(6)).resize((max(W, int(im.width * s)), max(H, int(im.height * s))), Image.BILINEAR)
    x0, y0 = (b.width - W) // 2, (b.height - H) // 2
    b = b.crop((x0, y0, x0 + W, y0 + H))
    return Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), b, 0.5).convert("RGBA")


def cam_speed(clip, t):
    a = cam_at(clip.get("cam"), t)
    b = cam_at(clip.get("cam"), t + 1 / FPS)
    if a[0] is None:
        return 0.0
    return math.hypot(a[0] - b[0], a[1] - b[1]) * a[2] + abs(math.log(b[2] / a[2])) * 900


# ------------------------------------------------------------------------------------------ subtitles
def render_subtitle(frame, sub, t, style):
    """sub: {start, end, words: [[text, emph(, s, e)], ...]} -> centred, wrapped line(s) that pop in.
    With style 'karaoke', the word being spoken is drawn in the highlight colour."""
    k = clamp((t - sub["start"]) / 0.09)
    size = int(style.get("size", 0.0556 * H))
    y = style.get("y", 0.896 * H)
    gap = size * 0.27
    color = style.get("color", "#FFFFFF")
    font, efont = style.get("font", "inter-semi"), style.get("emph_font", "inter-italic")
    shadow = style.get("shadow", 0.62)
    eglow = style.get("emph_glow", 0.55)
    ecolor = style.get("emph_color", color)
    hcolor = style.get("highlight_color")
    karaoke = style.get("karaoke") and hcolor
    box = style.get("box")
    imgs = []
    for w in sub["words"]:
        word, emph = w[0], bool(w[1])
        active = karaoke and len(w) >= 4 and w[2] <= t < max(w[3], w[2] + 0.12)
        c = hcolor if active else (ecolor if emph else color)
        if emph:
            im, pad, _ = text_layer(word, efont, size, c, 0, shadow, eglow)
        else:
            im, pad, _ = text_layer(word, font, size, c, 0, shadow, 0.0)
        imgs.append((im, pad))
    max_w = style.get("max_width", 0.86) * W
    lines, cur, cw = [], [], 0.0
    for im, pad in imgs:
        w = im.width - 2 * pad
        if cur and cw + gap + w > max_w:
            lines.append(cur)
            cur, cw = [], 0.0
        cw += (gap if cur else 0) + w
        cur.append((im, pad))
    if cur:
        lines.append(cur)
    lh = int(size * 1.3)
    block = Image.new("RGBA", (W, int(lh * len(lines) + size * 1.6)), (0, 0, 0, 0))
    for li, line in enumerate(lines):
        total = sum(im.width - 2 * pad for im, pad in line) + gap * (len(line) - 1)
        x = W / 2 - total / 2
        yc = int(size * 0.8 + lh * li + lh / 2)
        if box:
            ImageDraw.Draw(block).rounded_rectangle([x - size * 0.4, yc - lh / 2, x + total + size * 0.4, yc + lh / 2],
                                                    int(size * 0.25), fill=hex2rgba(box, int(style.get("box_alpha", 170))))
        for im, pad in line:
            block.alpha_composite(im, (int(round(x - pad)), int(yc - im.height / 2)))
            x += im.width - 2 * pad + gap
    scale = 0.94 + 0.06 * ease_back(clamp((t - sub["start"]) / 0.16), 1.6)
    yc = y - (len(lines) - 1) * lh / 2
    if not box and style.get("auto_box", True):
        # legibility on bright footage / images: add a translucent box when the area behind is light
        x0 = int(max(0, W / 2 - max_w / 2))
        y0 = int(max(0, yc - lh * len(lines) / 2))
        y1 = int(min(H, yc + lh * len(lines) / 2))
        region = np.asarray(frame.crop((x0, y0, int(min(W, W / 2 + max_w / 2)), max(y0 + 1, y1))).convert("L"))
        # bright (white text would vanish) or busy (e.g. a website's own text right behind the subtitle)
        if region.mean() > style.get("auto_box_threshold", 135) or region.std() > style.get("auto_box_busy", 42):
            backing = Image.new("RGBA", block.size, (0, 0, 0, 0))
            bd = ImageDraw.Draw(backing)
            for li, line in enumerate(lines):
                total = sum(im.width - 2 * pad for im, pad in line) + gap * (len(line) - 1)
                xl = W / 2 - total / 2
                yl = int(size * 0.8 + lh * li + lh / 2)
                bd.rounded_rectangle([xl - size * 0.45, yl - lh * 0.52, xl + total + size * 0.45, yl + lh * 0.52],
                                     int(size * 0.28), fill=(0, 0, 0, 150))
            backing.alpha_composite(block)
            block = backing
    paste_center(frame, block, W / 2, yc, k, scale)


# ------------------------------------------------------------------------------------------ watermark
def render_watermark(frame, mode, wm):
    if not wm or mode == "none":
        return
    text = wm.get("text", "")
    font = wm.get("font", "bebas")
    s = min(W, H) / 1080.0
    if mode == "card" and W >= H:
        icon = scaled_png(wm["icon"], int(wm.get("icon_h", 72) * s), 0.0, int(W * 0.12)) if wm.get("icon") else None
        if icon:
            paste_center(frame, icon, W - 208 * s, 322 * s, 0.85)
        if text:
            txt, pad, _ = text_layer(text, font, int(52 * s), wm.get("color", "#FFFFFF"), 2, 0.18)
            paste_center(frame, txt, W - min(320 * s, (txt.width - 2 * pad) / 2 + 40 * s), 452 * s, 0.85)
        return
    ic = scaled_png(wm["icon"], int(54 * s), 0.0, int(W * 0.10)) if wm.get("icon") else None
    x_right = W - 56 * s
    yrow = (H - 62 * s) if W >= H else 96 * s     # vertical: top-right, clear of platform UI at the bottom
    if text:
        txt, pad, _ = text_layer(text, font, int(38 * s), wm.get("color", "#FFFFFF"), 2, 0.6)
        tw = txt.width - 2 * pad
        paste_center(frame, txt, x_right - tw / 2, yrow + 2 * s, 0.78)
        x_right -= tw + 18 * s
    if ic:
        paste_center(frame, ic, x_right - ic.width / 2, yrow, 0.78)


# ------------------------------------------------------------------------------------------ compositing
def render_clip(clip, t, motion_blur=True):
    lt = t - clip["start"]
    typ = clip["type"]
    if typ in ("card", "flash"):
        img = render_card(clip, lt)
    elif typ == "endcard":
        img = render_endcard(clip, lt)
    elif typ in ("web", "video"):
        ss = 1
        if motion_blur and clip.get("cam"):
            v = cam_speed(clip, lt)
            ss = 1 if v < 12 else 4 if v < 60 else 8
        img = frame_footage(clip, lt, ss)
    elif typ == "still":
        img = render_still(clip, lt)
    else:
        raise ValueError(f"unknown clip type {typ}")
    for ov in clip.get("overlays", []):
        if ov.get("kind") == "image" and not isinstance(ov.get("src"), str):
            continue                      # defensive: never crash a render on a malformed overlay
        if ov["t"] <= lt < ov.get("until", 1e9):
            dt = lt - ov["t"]
            if ov["kind"] == "image":
                sc = 0.55 + 0.45 * ease_back(clamp(dt / 0.24), 2.2) if ov.get("anim", "pop") == "pop" else 1.0
                out_k = clamp((ov.get("until", 1e9) - lt) / 0.12)
                a_in = 1.0 if ov.get("anim") == "none" else clamp(dt / 0.08)
                paste_center(img, scaled_png(ov["src"], int(ov["h"]), float(ov.get("rot", 0)), int(ov.get("max_w", 0))),
                             ov["x"], ov["y"], a_in * out_k, sc)
            elif ov["kind"] == "text":
                paste_center(img, item_image(ov), ov["x"], ov["y"], clamp(dt / 0.08), 0.9 + 0.1 * ease_back(clamp(dt / 0.2)))
    return img


def whip(a_img, b_img, p, direction="up"):
    """Fast vertical slide with motion blur, p in [0, 1]."""
    e = ease_io(p)
    v = abs(ease_io(min(1, p + 0.02)) - ease_io(max(0, p - 0.02))) / 0.04
    off = int(round(e * H))
    A = np.asarray(a_img.convert("RGB"))
    B = np.asarray(b_img.convert("RGB"))
    out = np.empty_like(A)
    if direction == "up":
        out[: H - off] = A[off:]
        out[H - off:] = B[: off]
    else:
        out[off:] = A[: H - off]
        out[: off] = B[H - off:]
    k = int(max(1, v * H * 0.08))
    if k > 2:
        out = cv2.blur(out, (1, k))
    return Image.fromarray(out).convert("RGBA")


def zoom_blur(img, strength):
    if strength < 0.01:
        return img
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    acc = a.copy()
    n = 6
    for i in range(1, n):
        s = 1 + strength * i / n
        M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
        acc += cv2.warpAffine(a, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return Image.fromarray((acc / n).astype(np.uint8)).convert("RGBA")


DEFAULT_WM = {"card": "card", "flash": "card", "web": "corner", "video": "corner", "still": "corner", "endcard": "none"}


def _clip_at(clips, t, before=False):
    if before:
        return next((c for c in clips if c["start"] < t <= c["end"]), None)
    return next((c for c in clips if c["start"] <= t < c["end"]), None)


def render_frame(edl, t, motion_blur=True):
    clips = edl["clips"]
    cur = _clip_at(clips, t) or clips[-1]
    img = None
    for tr in edl.get("transitions", []):
        a0 = tr["at"] - tr.get("dur", 0) / 2
        a1 = tr["at"] + tr.get("dur", 0) / 2
        if not (a0 <= t < a1):
            continue
        ca = _clip_at(clips, tr["at"], before=True)
        cb = _clip_at(clips, tr["at"])
        if not ca or not cb:
            continue
        p = (t - a0) / max(1e-6, a1 - a0)
        if tr["kind"] == "whip":
            ia = render_clip(ca, min(t, ca["end"] - 1e-3), False)
            ib = render_clip(cb, max(t, cb["start"]), False)
            img = whip(ia, ib, p, tr.get("dir", "up"))
            cur = cb if t >= tr["at"] else ca
        elif tr["kind"] == "punch":
            c = cb if t >= tr["at"] else ca
            base = render_clip(c, t if t >= tr["at"] else min(t, c["end"] - 1e-3), False)
            img = zoom_blur(base, 0.22 * (1 - abs(2 * p - 1)))
            cur = c
        elif tr["kind"] == "fade":
            ia = render_clip(ca, min(t, ca["end"] - 1e-3), False)
            ib = render_clip(cb, max(t, cb["start"]), False)
            img = Image.blend(ia, ib, ease_io(p))
            cur = cb if t >= tr["at"] else ca
        if img is not None:
            break
    if img is None:
        img = render_clip(cur, t, motion_blur)
    sst = edl.get("subtitle_style", {})
    if sst.get("enabled", True) and cur.get("subs", cur["type"] in ("web", "video", "still")):
        for s in edl.get("subtitles", []):
            if s["start"] <= t < s["end"]:
                render_subtitle(img, s, t, sst)
                break
    render_watermark(img, cur.get("watermark", DEFAULT_WM.get(cur["type"], "none")), edl.get("watermark"))
    fin, fout = edl.get("fade_in", 0.0), edl.get("fade_out", 0.0)
    k = min(clamp(t / fin) if fin else 1, clamp((edl["duration"] - t) / fout) if fout else 1)
    if k < 1:
        img = Image.blend(Image.new("RGBA", (W, H), (0, 0, 0, 255)), img, k)
    return img
