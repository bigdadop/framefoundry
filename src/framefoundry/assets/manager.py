"""Asset management: scan user assets, prepare the logo, probe media, build edit proxies.

Images (.png .jpg .jpeg .webp), videos (.mp4 .mov .mkv .webm .m4v), fonts (.ttf .otf) are picked up from
--assets-dir. PNG/WebP images with real transparency are also offered to the editor as pop-in
"stickers" (cut-outs). Logos are only trimmed of transparent margins — never resized or distorted.
Video proxies (short GOP, exact seeking) are cached in tmp/proxy_cache and reused across campaigns.
"""
import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from framefoundry.util import ROOT, FrameFoundryError, ffmpeg, file_sig, info, media_info, sha_text, warn

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
FONT_EXT = {".ttf", ".otf"}


def has_transparency(im):
    if im.mode not in ("RGBA", "LA", "P"):
        return False
    a = np.asarray(im.convert("RGBA").getchannel("A"))
    return bool((a < 250).mean() > 0.05)


def prepare_logo(src, out_dir):
    if not os.path.isfile(src):
        raise FrameFoundryError(f"logo not found: {src}")
    try:
        im = Image.open(src)
        im.load()
    except Exception as e:  # noqa: BLE001
        raise FrameFoundryError(f"cannot read logo {src}: {e}") from None
    im = im.convert("RGBA")
    if has_transparency(im):
        bbox = im.getchannel("A").getbbox()
        if bbox and bbox != (0, 0, im.width, im.height):
            im = im.crop(bbox)
    else:
        warn("the logo has no transparency; it is used as-is (a transparent PNG looks best on cards and the end card)")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "logo.png")
    im.save(out)
    return out, {"w": im.width, "h": im.height, "transparent": has_transparency(im)}


def leading_black(path, probe_seconds=4.0):
    """Seconds of black at the very start of a clip (page-load frames, fade-ins) — 0.0 if none."""
    import re
    from framefoundry.util import ffmpeg_bin, run
    r = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-t", str(probe_seconds), "-i", str(path), "-an",
             "-vf", "blackdetect=d=0.03:pix_th=0.10", "-f", "null", "-"], check=False, capture=True)
    for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr):
        if float(s) < 0.05:
            return round(float(e) + 0.05, 3)
    return 0.0


def make_proxy(src, max_w=2560):
    """Short-GOP H.264 copy for fast exact seeking (cached by file signature)."""
    cache = ROOT / "tmp" / "proxy_cache"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{sha_text(file_sig(src), max_w)}.mp4"
    if out.exists() and out.stat().st_size > 1000:
        return str(out)
    tmp = str(out) + ".part.mp4"
    ffmpeg("-i", src, "-an", "-vf", f"scale='min({max_w},iw)':-2:flags=lanczos,fps=30,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-g", "15", "-movflags", "+faststart", tmp)
    os.replace(tmp, out)
    return str(out)


def scan(assets_dirs=(), files=(), logo=None, out_dir=None):
    """-> manifest {logo, logo_meta, images, videos, stickers, fonts, notes}"""
    man = {"logo": None, "logo_meta": None, "images": [], "videos": [], "stickers": [], "fonts": {}, "notes": []}
    out_dir = out_dir or str(ROOT / "tmp")
    asset_out = os.path.join(out_dir, "assets")
    os.makedirs(asset_out, exist_ok=True)
    paths = []
    for d in assets_dirs or ():
        p = Path(d)
        if not p.is_dir():
            raise FrameFoundryError(f"--assets-dir not found: {d}")
        paths += sorted(x for x in p.rglob("*") if x.is_file() and not x.name.startswith("."))
    paths += [Path(f) for f in files or ()]
    logo_real = os.path.realpath(logo) if logo else None
    if logo:
        man["logo"], man["logo_meta"] = prepare_logo(logo, os.path.join(asset_out, "brand"))
    n_img = n_vid = 0
    for p in paths:
        ext = p.suffix.lower()
        if logo_real and os.path.realpath(p) == logo_real:
            continue
        if ext in IMAGE_EXT:
            try:
                im = Image.open(p)
                im.load()
            except Exception as e:  # noqa: BLE001
                man["notes"].append(f"skipped unreadable image {p.name}: {e}")
                continue
            alpha = has_transparency(im)
            n_img += 1
            dst = os.path.join(asset_out, f"img{n_img:02d}_{p.name}")
            shutil.copy2(p, dst)
            entry = {"id": f"I{n_img:02d}", "name": p.name, "path": dst, "w": im.width, "h": im.height, "alpha": alpha}
            man["images"].append(entry)
            if alpha and min(im.width, im.height) >= 120:
                man["stickers"].append(dst)
        elif ext in VIDEO_EXT:
            try:
                mi = media_info(p)
            except FrameFoundryError as e:
                man["notes"].append(f"skipped unreadable video {p.name}: {e}")
                continue
            if not mi["has_video"] or not mi["duration"]:
                man["notes"].append(f"skipped {p.name}: no video stream")
                continue
            n_vid += 1
            info(f"preparing edit proxy for {p.name} ({mi['width']}x{mi['height']}, {mi['duration']:.1f}s)")
            man["videos"].append({"id": f"V{n_vid:02d}", "name": p.name, "path": str(p), "proxy": make_proxy(str(p)),
                                  "w": mi["width"], "h": mi["height"], "duration": mi["duration"], "has_audio": mi["has_audio"],
                                  "usable_start": leading_black(str(p))})
        elif ext in FONT_EXT:
            dst = os.path.join(asset_out, p.name)
            shutil.copy2(p, dst)
            man["fonts"][f"custom-{p.stem.lower()}"] = dst
        elif ext not in {".txt", ".md", ".json", ".yaml", ".yml", ".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
            man["notes"].append(f"ignored {p.name} (unsupported type)")
    return man


def screen_recording_entry(path, idx):
    """An uploaded screen recording -> a recording the editor can use like a website capture."""
    mi = media_info(path)
    if not mi["has_video"]:
        raise FrameFoundryError(f"--screen-recording {path} has no video stream")
    name = f"upload_{idx:02d}"
    entry = {"src": make_proxy(str(path)), "css_w": mi["width"], "css_h": mi["height"], "duration": mi["duration"],
             "marks": [], "cursor": [], "clicks": [], "usable_start": 0.0, "title": os.path.basename(str(path)),
             "source": "upload", "original": str(path)}
    side = Path(path).with_suffix(".json")          # a FrameFoundry recorder sidecar: step marks / cursor / clicks
    if side.is_file():
        try:
            import json
            meta = json.loads(side.read_text(encoding="utf-8"))
            dsf = float(meta.get("dsf") or 1.0)
            entry["css_w"] = int(meta.get("css_w") or round(mi["width"] / dsf))
            entry["css_h"] = int(meta.get("css_h") or round(mi["height"] / dsf))
            for k in ("marks", "cursor", "clicks"):
                if isinstance(meta.get(k), list):
                    entry[k] = meta[k]
            if entry["marks"]:
                entry["usable_start"] = max(0.0, float(entry["marks"][0][1]) - 0.2)
            info(f"  using step marks from {side.name} ({len(entry['marks'])} marks)")
        except (ValueError, OSError) as e:
            warn(f"ignored sidecar {side.name}: {e}")
    entry["usable_start"] = max(entry["usable_start"], leading_black(str(path)))   # never open on page-load black
    return name, entry
