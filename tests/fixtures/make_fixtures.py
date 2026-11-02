#!/usr/bin/env python3
"""Generate legitimate, licence-free test media procedurally (nothing downloaded, nothing private).

  python tests/fixtures/make_fixtures.py [OUT_DIR]     (default: tests/fixtures/generated)

Creates: logo.png (fictional brand "Lumen Notes", transparent), assets/ (two product-style images, a transparent
cut-out, a synthetic video clip), music_loop.wav (original 110 BPM loop synthesised here), site/ (a tiny local
website used for deterministic browser-recording tests).
"""
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FONTS = os.path.join(ROOT, "assets", "fonts")


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


def logo(path):
    im = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([20, 40, 240, 260], 48, fill=(37, 99, 235, 255))
    d.polygon([(80, 200), (130, 90), (180, 200)], fill=(255, 255, 255, 255))
    d.ellipse([112, 120, 148, 156], fill=(37, 99, 235, 255))
    d.text((280, 70), "LUMEN", font=font("Oswald-Variable.ttf", 120), fill=(17, 24, 39, 255))
    d.text((290, 205), "NOTES", font=font("Poppins-SemiBold.ttf", 44), fill=(219, 39, 119, 255))
    im.save(path)


def product_image(path, title, hue):
    w, h = 1600, 1000
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([(np.sin(x / 300 + hue) * 40 + 200), (np.cos(y / 260 + hue) * 40 + 190), np.full((h, w), 235.0)], -1)
    im = Image.fromarray(base.clip(0, 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([160, 140, 1440, 860], 40, fill=(255, 255, 255))
    d.rounded_rectangle([200, 190, 700, 820], 24, fill=(238, 243, 250))
    for i in range(6):
        d.rounded_rectangle([230, 230 + i * 95, 670, 290 + i * 95], 14, fill=(214, 226, 245) if i else (37, 99, 235))
    d.text((760, 220), title, font=font("Oswald-Variable.ttf", 86), fill=(17, 24, 39))
    for i in range(5):
        d.rounded_rectangle([760, 380 + i * 80, 1380 - i * 60, 420 + i * 80], 12, fill=(229, 231, 235))
    im.save(path, quality=92)


def cutout(path):
    im = Image.new("RGBA", (700, 700), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([100, 100, 600, 600], fill=(249, 115, 22, 255))
    d.ellipse([230, 260, 300, 330], fill=(17, 24, 39, 255))
    d.ellipse([400, 260, 470, 330], fill=(17, 24, 39, 255))
    d.arc([230, 300, 470, 480], 20, 160, fill=(17, 24, 39, 255), width=22)
    shadow = im.filter(ImageFilter.GaussianBlur(6))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.alpha_composite(shadow)
    out.alpha_composite(im)
    out.save(path)


def video(path, ffmpeg):
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "mandelbrot=size=1280x720:rate=30:inner=convergence", "-t", "8", "-vf", "hue=s=1.3,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", path], check=True)


def music(path, seconds=16.0, bpm=110, sr=48000):
    """An original loop: kick, snare, hats, bass and a pad chord progression."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    beat = 60 / bpm
    out = np.zeros(n)
    for k in range(int(seconds / beat)):
        s = int(k * beat * sr)
        L = int(0.25 * sr)
        tt = np.arange(min(L, n - s)) / sr
        out[s:s + len(tt)] += np.sin(2 * math.pi * (55 + 90 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 9) * 0.9   # kick
        if k % 2 == 1:
            nz = np.random.default_rng(k).standard_normal(len(tt))
            out[s:s + len(tt)] += nz * np.exp(-tt * 22) * 0.35                                                  # snare
        for h in (0, 0.5):
            hs = int((k + h) * beat * sr)
            hl = min(int(0.05 * sr), n - hs)
            if hl > 0:
                out[hs:hs + hl] += np.random.default_rng(k * 7 + int(h * 10)).standard_normal(hl) * np.exp(-np.arange(hl) / sr * 90) * 0.12
    chords = [(220.0, 277.18, 329.63), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63), (196.0, 246.94, 329.63)]
    bar = 4 * beat
    for i in range(int(seconds / bar) + 1):
        a, b = int(i * bar * sr), min(n, int((i + 1) * bar * sr))
        if a >= n:
            break
        tt = t[a:b] - t[a]
        env = np.minimum(1, tt * 4) * np.minimum(1, (bar - tt) * 4)
        for f in chords[i % 4]:
            out[a:b] += np.sin(2 * math.pi * f * tt) * 0.06 * env
        out[a:b] += np.sin(2 * math.pi * chords[i % 4][0] / 2 * tt) * 0.18 * env
    out /= np.abs(out).max() * 1.12
    import soundfile as sf
    sf.write(path, np.stack([out, out], 1).astype(np.float32), sr, subtype="PCM_16")


SITE = {
    "index.html": """<!doctype html><html><head><meta charset="utf-8"><title>Lumen Notes</title><style>
body{margin:0;font-family:sans-serif;background:#f7f5f0;color:#111827}nav{display:flex;gap:32px;padding:24px 64px;background:#fff;
position:sticky;top:0;box-shadow:0 2px 8px #0001}nav a{color:#2563eb;text-decoration:none;font-weight:600}
section{min-height:620px;padding:80px 64px}h1{font-size:72px;margin:0 0 24px}h2{font-size:48px}
.card{display:inline-block;width:300px;height:180px;margin:16px;border-radius:24px;background:#fff;box-shadow:0 6px 24px #0002;
padding:24px;vertical-align:top}.hero{background:linear-gradient(120deg,#dbeafe,#fce7f3)}button{font-size:22px;padding:14px 28px;
border-radius:14px;border:0;background:#db2777;color:#fff}</style></head><body>
<nav><a href="index.html">Home</a><a href="features.html">Features</a><a href="#pricing">Pricing</a><a href="#faq">FAQ</a></nav>
<section class="hero"><h1>Lumen Notes</h1><p style="font-size:28px">Capture ideas in seconds. Organise them automatically.</p>
<button>Sign up free</button></section>
<section><h2>Why teams love it</h2><div class="card"><h3>Instant capture</h3>Write, paste or speak.</div>
<div class="card"><h3>Smart folders</h3>Notes sort themselves.</div><div class="card"><h3>Share</h3>One link for your team.</div></section>
<section id="pricing"><h2>Pricing</h2><div class="card"><h3>Free</h3>Unlimited notes</div><div class="card"><h3>Team</h3>Shared spaces</div></section>
<section id="faq"><h2>FAQ</h2><p>Is it free? Yes, the personal plan is free forever.</p></section></body></html>""",
    "features.html": """<!doctype html><html><head><meta charset="utf-8"><title>Features</title><style>
body{margin:0;font-family:sans-serif;background:#eef3fa}section{padding:80px 64px;min-height:900px}h1{font-size:64px}
.f{background:#fff;border-radius:20px;padding:28px;margin:20px 0;font-size:26px}</style></head><body><section>
<h1>Features</h1><div class="f">Templates for meetings</div><div class="f">Offline mode</div><div class="f">Search everything</div>
</section></body></html>""",
}


def site(d):
    os.makedirs(d, exist_ok=True)
    for name, html in SITE.items():
        open(os.path.join(d, name), "w", encoding="utf-8").write(html)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "generated")
    ff = os.path.join(ROOT, ".framefoundry", "bin", "ffmpeg")
    ff = ff if os.path.exists(ff) else "ffmpeg"
    a = os.path.join(out, "assets")
    os.makedirs(a, exist_ok=True)
    logo(os.path.join(out, "logo.png"))
    product_image(os.path.join(a, "dashboard.jpg"), "Dashboard", 0.0)
    product_image(os.path.join(a, "smart_folders.jpg"), "Smart folders", 1.7)
    cutout(os.path.join(a, "mascot.png"))
    video(os.path.join(a, "demo_clip.mp4"), ff)
    music(os.path.join(out, "music_loop.wav"))
    site(os.path.join(out, "site"))
    print("fixtures written to", out)


if __name__ == "__main__":
    main()
