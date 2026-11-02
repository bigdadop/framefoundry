"""Shared helpers: logging, JSON, hashing, subprocess and FFmpeg/FFprobe access."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("FRAMEFOUNDRY_HOME") or Path(__file__).resolve().parents[2])
_T0 = time.time()
_LOG_FILE = None


class FrameFoundryError(Exception):
    """An error with a message meant for the user (no traceback needed)."""


# ------------------------------------------------------------------------------------------ logging
def set_log_file(path):
    global _LOG_FILE
    if _LOG_FILE:
        _LOG_FILE.close()
    _LOG_FILE = open(path, "a", encoding="utf-8") if path else None


def _emit(line, stream=sys.stdout):
    print(line, file=stream, flush=True)
    if _LOG_FILE:
        _LOG_FILE.write(time.strftime("%H:%M:%S ") + line + "\n")
        _LOG_FILE.flush()


def step(msg):
    _emit(f"\n==> [{time.time() - _T0:6.1f}s] {msg}")


def info(msg):
    _emit(f"    {msg}")


def warn(msg):
    _emit(f"    WARNING: {msg}", sys.stderr)


def progress(frac, msg=""):
    _emit(f"    [{frac * 100:5.1f}%] {msg}")


# ------------------------------------------------------------------------------------------ json / hashing
def jdump(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def sha_text(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str, ensure_ascii=False).encode())
        h.update(b"|")
    return h.hexdigest()[:20]


def sha_file(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        n = 0
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
            if limit and n >= limit:
                break
    return h.hexdigest()


def file_sig(path):
    try:
        st = os.stat(path)
        return f"{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return f"{path}:missing"


# ------------------------------------------------------------------------------------------ tools
def tool_path(name):
    """ffmpeg/ffprobe: the project's static build (.framefoundry/bin) first, then $FF_<NAME>, then PATH."""
    local = ROOT / ".framefoundry" / "bin" / name
    if local.exists():
        return str(local)
    env = os.environ.get(f"FF_{name.upper()}")
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    raise FrameFoundryError(f"{name} was not found. Run ./install.sh (it installs FFmpeg/FFprobe), "
                            f"or install your distribution's ffmpeg package.")


def ffmpeg_bin():
    return tool_path("ffmpeg")


def ffprobe_bin():
    return tool_path("ffprobe")


def run(cmd, check=True, capture=False, **kw):
    cmd = [str(c) for c in cmd]
    r = subprocess.run(cmd, capture_output=capture, text=True if capture else None, **kw)
    if check and r.returncode != 0:
        err = (r.stderr or "")[-2000:] if capture else ""
        raise FrameFoundryError(f"command failed ({r.returncode}): {' '.join(cmd[:6])} ...\n{err}")
    return r


def ffmpeg(*args, check=True, capture=True):
    return run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=check, capture=capture)


def probe(path):
    """ffprobe -> {'format':..., 'streams':[...]} (raises FrameFoundryError for unreadable media)."""
    r = run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
            check=False, capture=True)
    if r.returncode != 0:
        raise FrameFoundryError(f"cannot read media file {path}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout or "{}")


def media_info(path):
    """Compact description of a media file: duration, video size/fps, audio presence."""
    pr = probe(path)
    v = next((s for s in pr.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in pr.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = None
    if v:
        num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
        try:
            fps = float(num) / float(den or 1) if float(den or 1) else None
        except ValueError:
            fps = None
    dur = pr.get("format", {}).get("duration")
    return {"path": str(path), "duration": float(dur) if dur not in (None, "N/A") else None,
            "width": int(v["width"]) if v else None, "height": int(v["height"]) if v else None,
            "fps": round(fps, 3) if fps else None, "video_codec": v.get("codec_name") if v else None,
            "has_video": v is not None, "has_audio": a is not None,
            "audio_rate": int(a.get("sample_rate", 0)) if a else None,
            "audio_channels": int(a.get("channels", 0)) if a else None}


def media_duration(path):
    d = media_info(path)["duration"]
    if d is None:
        raise FrameFoundryError(f"cannot determine duration of {path}")
    return d


def cpu_count():
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 2


def util_versions():
    """FFmpeg version and where it comes from (system package or the project's static build)."""
    out = {}
    try:
        exe = ffmpeg_bin()
        r = run([exe, "-version"], check=False, capture=True)
        first = (r.stdout or "").splitlines()[0] if r.stdout else ""
        out["ffmpeg"] = first.replace("ffmpeg version ", "").split(" Copyright")[0]
        out["ffmpeg_source"] = "framefoundry static build" if ".framefoundry" in os.path.realpath(exe) else exe
    except FrameFoundryError:
        out["ffmpeg"] = None
    return out
