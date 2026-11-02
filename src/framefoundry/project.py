"""Campaign (output) folders: unique by default, never overwritten without --overwrite."""
import os
import re
import shutil
from pathlib import Path

from framefoundry.util import FrameFoundryError

SAFE = re.compile(r"[^A-Za-z0-9_\-]+")


def slug(text, default="campaign"):
    s = SAFE.sub("_", (text or "").strip()).strip("_").lower()
    return s[:48] or default


def campaign_dir(base, output=None, name_hint=None, overwrite=False):
    """--output NAME|PATH (exact folder; refuses an existing non-empty folder unless overwrite) or
    an automatic unique <hint>_NNN folder under base."""
    base = Path(base)
    if output:
        p = Path(output)
        if not p.is_absolute() and len(p.parts) == 1:
            p = base / p
        if p.exists() and any(p.iterdir()):
            if not overwrite:
                raise FrameFoundryError(f"output folder {p} already exists — choose another --output or add --overwrite")
            # only remove what FrameFoundry itself produced in that folder
            markers = ("manifest.json", "summary.json", "generation.log")      # generation.log is written first
            if not any((p / m).exists() for m in markers):
                raise FrameFoundryError(f"{p} exists and was not created by FrameFoundry; refusing to overwrite it")
            shutil.rmtree(p)
        p.mkdir(parents=True, exist_ok=True)
        return p
    hint = slug(name_hint)
    base.mkdir(parents=True, exist_ok=True)
    n = 1
    existing = [d.name for d in base.iterdir() if d.is_dir()]
    nums = [int(m.group(1)) for d in existing if (m := re.fullmatch(re.escape(hint) + r"_(\d{3,})", d))]
    if nums:
        n = max(nums) + 1
    while True:
        p = base / f"{hint}_{n:03d}"
        try:
            p.mkdir(parents=True)
            return p
        except FileExistsError:
            n += 1


def subdirs(root):
    d = {k: Path(root) / k for k in ("screen_recordings", "assets", "project_files")}
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def relpath(p, root):
    try:
        return os.path.relpath(p, root)
    except ValueError:
        return str(p)
