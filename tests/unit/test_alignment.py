"""Cross-script word alignment (no model needed: recognised words are given)."""
from framefoundry.audio.align import align, estimate, spoken_words

HINGLISH = "Apne ideas ko music mein convert karo, SunoSunao pe sirf kuch clicks mein.".split()
HEARD_DEV = [("अपने", .10, .35), ("आइडियाज", .36, .80), ("को", .81, .92), ("म्यूजिक", .93, 1.30), ("में", 1.31, 1.42),
             ("कन्वर्ट", 1.43, 1.85), ("करो", 1.86, 2.10), ("सुनो", 2.30, 2.55), ("सुनाओ", 2.56, 2.90), ("पे", 2.91, 3.00),
             ("कुछ", 3.20, 3.40), ("क्लिक्स", 3.41, 3.80), ("में", 3.81, 4.00)]


def test_one_entry_per_script_word_in_order():
    res = align(HINGLISH, HEARD_DEV)
    assert [r["w"] for r in res] == HINGLISH
    starts = [r["s"] for r in res]
    assert starts == sorted(starts)


def test_devanagari_recognition_matches_roman_script():
    res = {r["w"]: r for r in align(HINGLISH, HEARD_DEV)}
    assert res["karo,"]["matched"] and abs(res["karo,"]["s"] - 1.86) < 1e-6
    assert res["music"]["matched"]


def test_brand_split_into_two_heard_words():
    res = {r["w"]: r for r in align(HINGLISH, HEARD_DEV)}
    assert res["SunoSunao"]["matched"] and res["SunoSunao"]["s"] == 2.30 and res["SunoSunao"]["e"] == 2.90


def test_missing_word_is_interpolated_between_neighbours():
    res = {r["w"]: r for r in align(HINGLISH, HEARD_DEV)}
    assert not res["sirf"]["matched"]
    assert 3.0 <= res["sirf"]["s"] <= res["sirf"]["e"] <= 3.2


def test_english_alignment():
    script = "Capture any idea in seconds".split()
    heard = [("capture", 0.1, 0.5), ("any", 0.5, 0.7), ("idea", 0.7, 1.0), ("in", 1.0, 1.1), ("seconds", 1.1, 1.6)]
    res = align(script, heard)
    assert all(r["matched"] for r in res)


def test_estimate_without_recognition():
    res = estimate(["one", "two", "three"], 1.0, 4.0)
    assert res[0]["s"] == 1.0 and abs(res[-1]["e"] - 4.0) < 1e-6


def test_estimate_over_speech_regions_skips_pauses():
    res = estimate(["aaaa", "bbbb"], 0, 10, regions=[(0.0, 1.0), (5.0, 6.0)])
    assert res[0]["e"] <= 1.0 + 1e-6 and res[1]["s"] >= 5.0 - 1e-6


def test_spoken_words_strip_emphasis_and_dashes():
    assert spoken_words("Naam hai *SunoSunao* — AI!") == ["Naam", "hai", "SunoSunao", "AI!"]


def test_snap_word_starts_to_speech_onsets():
    from framefoundry.audio.align import snap_to_onsets
    words = [{"w": "a", "s": 0.80, "e": 1.40}, {"w": "b", "s": 1.42, "e": 1.90}, {"w": "c", "s": 3.00, "e": 3.50}]
    regions = [(1.00, 1.95), (3.60, 4.20)]
    out = snap_to_onsets(words, regions)
    assert out[0]["s"] == 1.00            # started in silence 0.2 s before the onset -> snapped
    assert out[1]["s"] == 1.42            # already inside speech -> unchanged
    assert out[2]["s"] == 3.00            # onset 0.6 s away (> 0.35) -> not moved
