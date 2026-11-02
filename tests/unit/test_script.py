"""Script format parsing and scene planning."""
from framefoundry.editing.styles import BASE_PROFILE
from framefoundry.script import parser as P
from framefoundry.script import planner

SCRIPT = """---
brand: Acme
tagline: Make it yours
website: https://acme.example
---
# Hook
Too many *ideas*?
// a comment
- Notes everywhere.

# Product
[web] Open the dashboard. {tts: Open the dash board.}
[image:shot.png] Smart folders sort everything.
Meet Acme.

# CTA
Try it free today.
Go!
"""


def test_front_matter_and_lines():
    meta, lines = P.parse(SCRIPT)
    assert meta == {"brand": "Acme", "tagline": "Make it yours", "website": "https://acme.example"}
    assert [L["plain"] for L in lines] == ["Too many ideas?", "Notes everywhere.", "Open the dashboard.",
                                           "Smart folders sort everything.", "Meet Acme.", "Try it free today.", "Go!"]
    assert lines[2]["tag"] == "web" and lines[2]["tts"] == "Open the dash board."
    assert lines[3]["tag"] == "image" and lines[3]["target"] == "shot.png"
    assert lines[0]["section"] == "hook" and lines[-1]["section"] == "cta"
    assert [L["id"] for L in lines][:3] == ["L01", "L02", "L03"]


def test_emphasis_kept_in_text_not_plain():
    _, lines = P.parse("Bas tumhara *idea* chahiye.")
    assert lines[0]["text"] == "Bas tumhara *idea* chahiye." and lines[0]["plain"] == "Bas tumhara idea chahiye."


def test_split_long_lines():
    long = " ".join(["word"] * 10) + ", " + " ".join(["more"] * 12) + "."
    _, lines = P.parse(long)
    out = P.split_long(lines, max_words=18)
    assert len(out) == 2 and out[0]["text"].endswith(",")


def test_round_trip_keeps_tags_and_sections():
    meta, lines = P.parse(SCRIPT)
    meta2, lines2 = P.parse(P.lines_to_text(lines, meta))
    assert meta2 == meta
    assert [(L["plain"], L["tag"], L["tts"], L["section"]) for L in lines2] == \
           [(L["plain"], L["tag"], L["tts"], L["section"]) for L in lines]


def test_find_variant(tmp_path):
    s = tmp_path / "script.txt"
    s.write_text("Hello.")
    (tmp_path / "script.hindi.txt").write_text("नमस्ते।")
    assert P.find_variant(s, "hindi").name == "script.hindi.txt"
    assert P.find_variant(s, "hinglish") is None


def test_planner_roles_and_tags():
    _, lines = P.parse(SCRIPT)
    ctx = {"style": BASE_PROFILE, "brand": {"name": "Acme", "logo": "logo.png"},
           "recordings": [{"name": "rec_01", "title": "site", "steps": []}],
           "images": [{"id": "I01", "name": "shot.png"}], "videos": []}
    sc = planner.plan_scenes(lines, ctx)
    kinds = [s["visual"]["type"] for s in sc]
    assert kinds[2] == "web" and sc[2]["visual"]["recording"] == "rec_01"
    assert kinds[3] == "image" and sc[3]["visual"]["asset_id"] == "I01"
    assert kinds[4] == "logo"                      # "Meet Acme." -> logo reveal
    assert kinds[0] == "card" and kinds[-1] == "flash"


def test_planner_without_assets_uses_cards():
    _, lines = P.parse("One line here.\nSecond line here.\nThird.\nFourth line.\nFifth.")
    sc = planner.plan_scenes(lines, {"style": BASE_PROFILE, "brand": {}, "recordings": [], "images": [], "videos": []})
    assert {s["visual"]["type"] for s in sc} <= {"card", "flash"}
