"""Automatic editor: contiguous timelines, subtitles inside the video, aspect-ratio aware layouts."""
import pytest
from PIL import Image

from framefoundry.audio.align import estimate, spoken_words
from framefoundry.editing import editor
from framefoundry.editing.styles import BASE_PROFILE
from framefoundry.script import parser as P
from framefoundry.script import planner

SCRIPT = """# Hook
Too many *ideas*, nowhere to keep them?
Notes everywhere.
# Product
Meet Acme.
[image:shot.png] Smart folders sort your notes automatically.
[web] See every project in one place.
# CTA
Start free today.
Go!
"""


def _timing(lines):
    t, out = 0.15, {}
    for L in lines:
        ws = spoken_words(L["plain"])
        d = 0.35 * len(ws) + 0.3
        est = estimate(ws, t, t + d)
        out[L["id"]] = {"start": round(t, 3), "end": round(t + d, 3), "words": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in est]}
        t += d + 0.15
    return out


@pytest.fixture
def plan_inputs(tmp_path):
    img = tmp_path / "shot.png"
    Image.new("RGB", (1600, 1000), (200, 210, 230)).save(img)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (600, 200), (20, 20, 20, 255)).save(logo)
    _, lines = P.parse(SCRIPT)
    rec = {"rec_01": {"src": str(tmp_path / "rec.mp4"), "css_w": 1920, "css_h": 1080, "duration": 12.0, "marks": [],
                      "cursor": [(0, 900, 500), (5, 1200, 600)], "clicks": [(3.0, 1200, 600)], "usable_start": 0.0, "title": "site"}}
    return tmp_path, lines, img, logo, rec


@pytest.mark.parametrize("w,h", [(1920, 1080), (1080, 1920), (1080, 1080)])
def test_edl_for_each_aspect(plan_inputs, w, h):
    root, lines, img, logo, rec = plan_inputs
    ctx = {"style": BASE_PROFILE, "brand": {"name": "Acme", "logo": str(logo)},
           "recordings": [{"name": "rec_01", "title": "site", "steps": []}], "images": [{"id": "I01", "name": "shot.png"}], "videos": []}
    scenes = planner.plan_scenes(lines, ctx)
    plan = {"root": str(root), "output": {"w": w, "h": h, "fps": 30}, "style": BASE_PROFILE, "scenes": scenes, "recordings": rec,
            "assets": {"I01": {"id": "I01", "name": "shot.png", "path": str(img), "w": 1600, "h": 1000, "alpha": False}},
            "stickers": [], "fonts": {}, "brand": {"name": "Acme", "handle": "acme.example", "tagline": "Make it yours",
                                                   "bg_word": "Acme", "logo": str(logo), "logo_icon": str(logo)}}
    timing = _timing(lines)
    edl = editor.build(plan, timing)
    clips = edl["clips"]
    assert clips[0]["start"] == 0.0 and clips[-1]["type"] == "endcard"
    for a, b in zip(clips, clips[1:]):
        assert abs(a["end"] - b["start"]) < 0.002
    assert edl["duration"] >= max(t["end"] for t in timing.values())
    for s in edl["subtitles"]:
        assert 0 <= s["start"] < s["end"] <= edl["duration"]
        assert all(len(wd) == 4 for wd in s["words"])          # per-word times for karaoke highlighting
    web = [c for c in clips if c["type"] == "web"]
    assert web and web[0]["layout"] == ("full" if w > h * 1.05 else "panel")
    end = clips[-1]
    assert end.get("logo_plate"), "a dark logo on the dark end card must get a contrast plate"
    assert not editor.needs_logo_plate(str(img), "#FFFFFF"), "never on a light end card"
    kinds = {c["type"] for c in clips}
    assert {"card", "still", "web", "endcard"} <= kinds


def test_tokens_with_emphasis():
    assert editor.tokens_with_emph("Naam hai *SunoSunao AI.*") == [("Naam", False), ("hai", False), ("SunoSunao", True), ("AI.", True)]


def test_display_words_map_onto_spoken_words():
    disp = [("₹2,500", False), ("prize", True)]
    spoken = [{"w": "पच्चीस", "s": 1.0, "e": 1.3}, {"w": "सौ", "s": 1.3, "e": 1.5}, {"w": "रुपये", "s": 1.5, "e": 1.9},
              {"w": "prize", "s": 2.0, "e": 2.4}]
    m = editor.map_display_words(disp, spoken, 1.0, 2.4)
    assert m[0][2] == pytest.approx(1.0) and m[-1][3] == pytest.approx(2.4)


def test_many_web_scenes_without_stickers(plan_inputs):
    """Regression: every third website scene is flagged for a sticker overlay; with no stickers the flag must
    not end up as an image source (it crashed the renderer)."""
    root, _, img, logo, rec = plan_inputs
    _, lines = P.parse("\n".join(f"[web] Feature number {i} is great." for i in range(1, 7)))
    ctx = {"style": BASE_PROFILE, "brand": {"name": "", "logo": None},
           "recordings": [{"name": "rec_01", "title": "site", "steps": []}], "images": [], "videos": []}
    scenes = planner.plan_scenes(lines, ctx)
    assert sum(1 for s in scenes if s.get("sticker_overlay")) >= 2
    for stickers in ([], [str(img)]):
        plan = {"root": str(root), "output": {"w": 1920, "h": 1080, "fps": 30}, "style": BASE_PROFILE, "scenes": scenes,
                "recordings": rec, "assets": {}, "stickers": stickers, "fonts": {}, "brand": {}}
        edl = editor.build(plan, _timing(lines))
        for c in edl["clips"]:
            for ov in c.get("overlays", []):
                assert isinstance(ov["src"], str)
        assert any(c.get("overlays") for c in edl["clips"]) == bool(stickers)
