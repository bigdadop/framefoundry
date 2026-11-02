"""Integration tests: real FFmpeg rendering, audio mixing and Chromium recording (no model weights needed)."""
import functools
import http.server
import json
import threading

import numpy as np
import pytest
import soundfile as sf

from framefoundry.config import get_config
from framefoundry.util import ROOT, media_info

pytestmark = pytest.mark.integration
FIX = ROOT / "tests" / "fixtures" / "generated"


@pytest.fixture(scope="session", autouse=True)
def fixtures():
    if not (FIX / "music_loop.wav").exists():
        import subprocess
        import sys
        subprocess.run([sys.executable, str(ROOT / "tests" / "fixtures" / "make_fixtures.py")], check=True)
    get_config().apply_environment()


def _edl(tmp, w=640, h=360):
    from PIL import Image
    img = tmp / "img.png"
    Image.new("RGB", (800, 500), (40, 90, 200)).save(img)
    return {"root": str(tmp), "w": w, "h": h, "fps": 30, "duration": 2.0, "fade_out": 0.3, "voice_end": 1.5, "sting_at": 1.6,
            "clips": [{"type": "card", "start": 0.0, "end": 1.0, "bg": "#FFF3DB", "bgword": "TEST", "bgword_color": "#F0E0C0",
                       "items": [{"kind": "text", "text": "हिंदी TEST", "t": 0.1, "x": w / 2, "y": h / 2, "size": 50,
                                  "font": "oswald-bold", "color": "#B12D6F", "anim": "slam"}]},
                      {"type": "still", "start": 1.0, "end": 2.0, "src": str(img), "fit": "contain", "z0": 1.0, "z1": 1.08}],
            "transitions": [{"at": 1.0, "kind": "whip", "dur": 0.2, "dir": "up"}],
            "subtitles": [{"start": 1.1, "end": 1.9, "words": [["subtitle", False, 1.1, 1.5], ["test", True, 1.5, 1.9]]}],
            "subtitle_style": {"size": 30, "y": 320}, "watermark": None}


def test_render_and_qa(tmp_path):
    from framefoundry.qa.check import check
    from framefoundry.rendering.render import render_parallel
    edl = _edl(tmp_path)
    p = tmp_path / "edl.json"
    p.write_text(json.dumps(edl))
    sr = 48000
    t = np.arange(int(2.0 * sr)) / sr
    tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    wav = tmp_path / "a.wav"
    sf.write(wav, np.stack([tone, tone], 1), sr)
    out = tmp_path / "v.mp4"
    render_parallel(str(p), str(wav), str(out), jobs=2, crf=23, preset="veryfast")
    mi = media_info(out)
    assert (mi["width"], mi["height"], mi["fps"]) == (640, 360, 30.0) and mi["has_audio"]
    qa = check(str(out), edl, {"target_lufs": -14})
    names = {c["name"]: c for c in qa["checks"]}
    for n in ("video codec H.264", "resolution", "frame rate", "duration matches edit", "decodes cleanly",
              "audio/video length in sync", "no unexpected black frames"):
        assert names[n]["ok"], (n, names[n])


def test_mix_levels_and_true_peak(tmp_path):
    from framefoundry.audio.mix import mix
    from framefoundry.audio.process import encode_aac_safe
    sr = 48000
    t = np.arange(int(4.0 * sr)) / sr
    voice = (0.3 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 3 * t) > 0)).astype(np.float32)
    vp = tmp_path / "voice.wav"
    sf.write(vp, voice, sr)
    cfg = get_config()
    final, info = mix(str(vp), 6.0, str(tmp_path), cfg, str(FIX / "music_loop.wav"), {"bed_db": -6})
    aac, st, gain = encode_aac_safe(final, str(tmp_path / "a.m4a"))
    assert abs(media_info(final)["duration"] - 6.0) < 0.05
    assert -15.6 <= st["integrated_lufs"] <= -12.4
    assert st["true_peak_dbtp"] <= -1.0
    bg = media_info(tmp_path / "background_music.wav")
    assert bg["audio_rate"] == 48000          # the original pipeline exported the music bed at 192 kHz


def _serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_record_local_website(tmp_path):
    from framefoundry.capture import website as WB
    httpd = _serve(FIX / "site")
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
        cfg = dict(get_config().main["browser"], max_seconds=25, device_scale_factor=1.0)
        steps = WB.parse_instructions("hover Pricing; scroll down; click Features; scroll down", url)
        meta = WB.record(url, str(tmp_path), cfg, steps, "rec_test")
    finally:
        httpd.shutdown()
    assert meta["status"] == "ok", meta.get("error")
    mi = media_info(meta["src"])
    assert mi["width"] == 1920 and mi["fps"] == 30.0 and mi["duration"] > 3
    labels = [m[0] for m in meta["marks"]]
    assert any("Features" in l for l in labels)
    assert meta["cursor"] and meta["clicks"], "cursor positions and clicks are logged for the camera"
    assert (tmp_path / "playwright_native" / "rec_test.webm").exists()


def test_unreachable_website_fails_gracefully(tmp_path):
    from framefoundry.capture import website as WB
    cfg = dict(get_config().main["browser"], navigation_timeout=8, max_seconds=10, device_scale_factor=1.0)
    meta = WB.record("http://127.0.0.1:9/", str(tmp_path), cfg, [{"action": "scroll", "to": "down"}], "rec_fail")
    assert meta["status"] == "error" and meta.get("error")


def test_login_wall_is_reported_not_bypassed(tmp_path):
    from framefoundry.capture import website as WB
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text("<html><body><form><input type=email><input type=password>"
                                                  "<button>Log in</button></form></body></html>")
    httpd = _serve(tmp_path / "site")
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
        cfg = dict(get_config().main["browser"], max_seconds=10, device_scale_factor=1.0)
        meta = WB.record(url, str(tmp_path / "out"), cfg, [{"action": "click", "text": "Log in"}], "rec_login")
    finally:
        httpd.shutdown()
    assert meta["status"] == "login_required"
    assert any("Log in" in s for s in meta["skipped"]), "a 'log in' submit button must not be clicked"
