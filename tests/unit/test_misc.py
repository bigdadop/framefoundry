"""Config, campaign folders, CLI validation and text rendering."""
import subprocess
import sys

import numpy as np
import pytest

from framefoundry.config import Config
from framefoundry.project import campaign_dir
from framefoundry.rendering import compositor as C
from framefoundry.util import ROOT, FrameFoundryError


def test_default_config_loads():
    c = Config()
    assert c.main["output"]["fps"] == 30 and c.main["output"]["video_codec"] == "libx264"
    assert c.voice["allow_non_commercial_models"] is False
    assert c.language["all_languages"] == ["english", "hindi", "hinglish"]


@pytest.mark.parametrize("aspect,size", [("16:9", (1920, 1080)), ("9:16", (1080, 1920)), ("1:1", (1080, 1080))])
def test_aspect_presets(aspect, size):
    assert Config().resolution(aspect) == size


def test_custom_resolution_and_errors():
    c = Config()
    assert c.resolution("16:9", "1281x721") == (1280, 720)
    with pytest.raises(FrameFoundryError):
        c.resolution("21:9")
    with pytest.raises(FrameFoundryError):
        c.resolution("16:9", "big")


def test_campaign_dirs_are_unique_and_protected(tmp_path):
    a = campaign_dir(tmp_path, None, "My Promo!")
    b = campaign_dir(tmp_path, None, "My Promo!")
    assert a.name == "my_promo_001" and b.name == "my_promo_002"
    named = campaign_dir(tmp_path, "launch")
    (named / "final_video.mp4").write_text("x")
    with pytest.raises(FrameFoundryError):
        campaign_dir(tmp_path, "launch")
    with pytest.raises(FrameFoundryError):                      # not created by FrameFoundry (no manifest)
        campaign_dir(tmp_path, "launch", overwrite=True)
    (named / "manifest.json").write_text("{}")
    assert campaign_dir(tmp_path, "launch", overwrite=True).exists()
    crashed = campaign_dir(tmp_path, "crashed")                 # a run that died early only left its log
    (crashed / "generation.log").write_text("x")
    (crashed / "english").mkdir()
    assert campaign_dir(tmp_path, "crashed", overwrite=True).exists()


def test_devanagari_renders_with_shaping_font():
    img, pad, _ = C.text_layer("संगीत में बदलें", "oswald-bold", 80, "#000000")
    a = np.asarray(img.getchannel("A"))
    assert a.max() == 255 and (a > 0).mean() > 0.05
    assert C.font_for("oswald-bold", "संगीत") == "teko-bold"
    assert C.font_for("inter-semi", "music") == "inter-semi"


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "framefoundry", *args], capture_output=True, text=True,
                          env={"PYTHONPATH": str(ROOT / "src"), "PATH": f"{ROOT / '.framefoundry' / 'bin'}:/usr/bin:/bin"})


def test_cli_help():
    r = _cli("generate", "--help")
    assert r.returncode == 0 and "--all-languages" in r.stdout and "--voiceover" in r.stdout


def test_cli_requires_script_or_voiceover():
    r = _cli("generate")
    assert r.returncode == 1 and "--script" in r.stderr


def test_cli_missing_file_is_reported():
    r = _cli("generate", "--script", "/nonexistent/script.txt")
    assert r.returncode == 1 and "not found" in r.stderr


def test_cli_voice_cloning_needs_permission(tmp_path):
    s = tmp_path / "s.txt"
    s.write_text("Hello there.")
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    r = _cli("generate", "--script", str(s), "--voice-reference", str(ref))
    assert r.returncode == 1 and "--confirm-voice-permission" in r.stderr


def test_cli_rejects_bad_language():
    r = _cli("generate", "--script", "x.txt", "--language", "french")
    assert r.returncode == 2


def test_noncommercial_engine_is_gated():
    from framefoundry.voice import engines as E
    c = Config()
    with pytest.raises(FrameFoundryError, match="NON-COMMERCIAL"):
        E.select_engine(c, "omnivoice")


def test_production_directives():
    import copy
    from framefoundry.editing.styles import BASE_PROFILE
    from framefoundry.pipeline import apply_directives, parse_instructions
    d, rest = parse_instructions(r"subtitles: karaoke\ntransitions: fade; music_volume_db: -8; make it punchy")
    assert d == {"subtitles": "karaoke", "transitions": "fade", "music_volume_db": "-8"} and rest == ["make it punchy"]
    prof = copy.deepcopy(BASE_PROFILE)
    apply_directives(prof, d, [])
    assert prof["subtitles"]["karaoke"] and prof["transitions"]["web_web"] == "fade" and prof["music"]["bed_db"] == -8.0
