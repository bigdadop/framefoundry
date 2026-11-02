"""`./framefoundry.sh doctor` — verify the installation and report what works."""
import importlib.util
import json
import os
import platform
import shutil
import subprocess

from framefoundry.util import ROOT, FrameFoundryError, ffmpeg_bin, ffprobe_bin, run

OK, WARN, FAIL = "PASS", "WARN", "FAIL"


def run_doctor(verbose=False):
    rows = []

    def add(status, name, detail):
        rows.append((status, name, detail))
    import sys
    add(OK if sys.version_info[:2] == (3, 12) else WARN, "Python", f"{platform.python_version()} at {sys.executable}")
    if "conda" in sys.executable or "miniconda" in sys.executable:
        add(WARN, "Python environment", "running from a Conda interpreter; use ./generate_video.sh (it uses .venv)")
    try:
        from framefoundry.config import get_config
        cfg = get_config()
        cfg.apply_environment()
        add(OK, "configuration", f"{cfg.dir}/config.yaml, voice_config.yaml, language_config.yaml")
    except FrameFoundryError as e:
        add(FAIL, "configuration", str(e))
        cfg = None
    try:
        ff, fp = ffmpeg_bin(), ffprobe_bin()
        v = run([ff, "-version"], capture=True).stdout.splitlines()[0]
        enc = run([ff, "-hide_banner", "-encoders"], capture=True).stdout
        flt = run([ff, "-hide_banner", "-filters"], capture=True).stdout
        need_f = ["loudnorm", "sidechaincompress", "ebur128", "blackdetect", "freezedetect", "amix"]
        miss = [x for x in need_f if f" {x} " not in flt]
        has264 = " libx264 " in enc
        add(OK if has264 and not miss else FAIL, "FFmpeg", f"{v.split(' Copyright')[0]} ({ff})" +
            ("" if has264 else " — NO libx264") + (f" — missing filters {miss}" if miss else ""))
        add(OK, "FFprobe", fp)
        hw = [e for e in ("h264_vaapi", "h264_nvenc") if f" {e} " in enc]
        add(OK, "hardware encoders compiled in", ", ".join(hw) or "none (CPU libx264 is used)")
    except FrameFoundryError as e:
        add(FAIL, "FFmpeg", str(e))
    from PIL import features
    add(OK if features.check("raqm") else FAIL, "text shaping (raqm)",
        "Devanagari conjuncts render correctly" if features.check("raqm") else "missing: Hindi text would render incorrectly")
    fonts = ROOT / "assets" / "fonts"
    need = ["Inter-Variable.ttf", "Oswald-Variable.ttf", "BebasNeue-Regular.ttf", "Poppins-SemiBold.ttf", "Teko-Variable.ttf"]
    miss = [f for f in need if not (fonts / f).exists()]
    add(FAIL if miss else OK, "fonts", f"missing {miss}" if miss else "Latin + Devanagari fonts present (SIL OFL)")
    for mod, name, sev in (("cv2", "OpenCV", FAIL), ("numpy", "numpy", FAIL), ("soundfile", "soundfile", FAIL),
                           ("librosa", "librosa", FAIL), ("faster_whisper", "faster-whisper (alignment)", FAIL),
                           ("playwright", "Playwright", FAIL), ("torch", "PyTorch (ML extras)", WARN),
                           ("demucs", "Demucs (music separation)", WARN), ("transformers", "transformers (translation)", WARN)):
        found = importlib.util.find_spec(mod) is not None
        add(OK if found else sev, name, "importable" if found else "not installed")
    # Chromium
    try:
        code = ("from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(headless=True)\n"
                " pg=b.new_page(); pg.set_content('<p>x</p>'); v=b.version; b.close(); print(v)")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=90)
        add(OK if r.returncode == 0 else FAIL, "Chromium (headless)", r.stdout.strip() if r.returncode == 0 else r.stderr.strip()[-300:])
    except Exception as e:  # noqa: BLE001
        add(FAIL, "Chromium (headless)", str(e))
    # models + voice engines
    if cfg:
        from framefoundry.voice import engines as E
        for name, e in E.ENGINES.items():
            ok, why = E.engine_status(name)
            lic = "commercial use OK" if e["commercial"] else "NON-COMMERCIAL weights"
            add(OK if ok else WARN, f"voice engine {name}", f"{'installed' if ok else why} — {lic}")
        hub = cfg.path("models") / "huggingface" / "hub"
        wm = cfg.main["alignment"]["whisper_model"]
        have = sorted(p.name.replace("models--", "").replace("--", "/") for p in hub.glob("models--*")) if hub.is_dir() else []
        add(OK if have else WARN, "downloaded models", ", ".join(have) or f"none yet (downloaded on first use; Whisper '{wm}')")
    # hardware
    gpu = "none detected"
    if shutil.which("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], capture_output=True, text=True)
        gpu = r.stdout.strip() or "nvidia-smi present but no GPU reported"
    elif os.path.exists("/proc/driver/nvidia/version"):
        gpu = "NVIDIA driver present"
    else:
        try:
            lspci = subprocess.run(["lspci"], capture_output=True, text=True).stdout
            nv = [l for l in lspci.splitlines() if "NVIDIA" in l and ("VGA" in l or "3D" in l)]
            if nv:
                gpu = "NVIDIA GPU present but the NVIDIA driver is not loaded (CPU mode)"
        except OSError:
            pass
    add(OK, "GPU acceleration", gpu)
    st = shutil.disk_usage(ROOT)
    add(OK if st.free > 15e9 else WARN, "free disk space", f"{st.free / 1e9:.0f} GB")
    state = ROOT / ".framefoundry" / "install_state.json"
    if state.exists():
        s = json.load(open(state))
        add(OK, "install state", f"installed {s.get('installed_at')}, components {s.get('components')}")
    width = max(len(r[1]) for r in rows)
    for status, name, detail in rows:
        print(f"  [{status}] {name:{width}s}  {detail}")
    fails = [r for r in rows if r[0] == FAIL]
    print(f"\n  {len(rows) - len(fails)}/{len(rows)} checks without failures" + (f"; {len(fails)} FAILED" if fails else ""))
    return 1 if fails else 0
