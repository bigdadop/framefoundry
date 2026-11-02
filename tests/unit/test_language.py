"""Language detection, transliteration and spoken-form normalisation."""
import pytest

from framefoundry.language import detect as D
from framefoundry.language import hinglish as H
from framefoundry.language import normalize as N

EXAMPLES = {
    "english": "Turn your imagination into music with just a few clicks.",
    "hindi": "सिर्फ कुछ क्लिक में अपने विचारों को संगीत में बदलें।",
    "romanised": "Sirf kuch clicks mein apne ideas ko music mein badlein.",
    "hinglish": "Apne ideas ko music mein convert karo, sirf kuch clicks mein.",
}


def test_english_detected():
    r = D.detect(EXAMPLES["english"])
    assert r["language"] == "english" and r["script"] == "latin"


def test_devanagari_hindi_detected():
    r = D.detect(EXAMPLES["hindi"])
    assert r["language"] == "hindi" and r["script"] == "devanagari" and r["variant"] == "devanagari_hindi"


def test_romanised_hindi_is_not_english():
    r = D.detect(EXAMPLES["romanised"])
    assert r["language"] == "hinglish"
    assert r["variant"] == "romanised_hindi"


def test_code_mixed_hinglish():
    r = D.detect(EXAMPLES["hinglish"])
    assert r["language"] == "hinglish" and r["variant"] == "code_mixed"


def test_mixed_script_sentence():
    r = D.classify_sentence("Apne ideas ko संगीत mein badlo")
    assert r["language"] == "hinglish"


def test_word_level_tags_keep_every_word():
    r = D.classify_sentence(EXAMPLES["hinglish"])
    assert [w["w"] for w in r["words"]] == ["Apne", "ideas", "ko", "music", "mein", "convert", "karo", "sirf", "kuch", "clicks", "mein"]
    tags = {w["w"]: w["type"] for w in r["words"]}
    assert tags["karo"] == "hindi_roman" and tags["convert"] == "english"


def test_compatible_languages():
    assert D.compatible("hindi", "hinglish") and D.compatible("hinglish", "hindi")
    assert not D.compatible("english", "hindi")


def test_resolve_language_rejects_unknown():
    with pytest.raises(ValueError):
        D.resolve_language("french", "bonjour")


def test_roman_to_devanagari_for_tts_keeps_english_words():
    out = H.hindi_words_to_devanagari(EXAMPLES["hinglish"])
    assert out.startswith("अपने ideas को music में convert करो")
    assert "clicks" in out


def test_devanagari_to_casual_roman():
    out = H.transliterate_text("सिर्फ कुछ क्लिक में अपने विचारों को संगीत में बदलें।", "roman")
    assert out.startswith("sirf kuch")
    assert " mein " in out and out.endswith(".")
    assert "।" not in out


@pytest.mark.parametrize("dev,roman", [("संगीत", "sangeet"), ("में", "mein"), ("करो", "karo"), ("नहीं", "nahin")])
def test_match_key_crosses_scripts(dev, roman):
    assert H.match_key(dev) == H.match_key(roman)


def test_numbers_spoken_hindi_and_english():
    assert N.spoken_tokens("₹2,500", "hindi") == ["पच्चीस", "सौ", "रुपये"]
    assert N.spoken_tokens("₹2,500", "english") == ["two", "thousand", "five", "hundred", "rupees"]
    assert N.spoken_tokens("50%", "english") == ["fifty", "percent"]


@pytest.mark.parametrize("text,expected", [
    ("Itne saare ideas, aur rakhne ki koi jagah nahi?", "इतने सारे ideas, और रखने की कोई जगह नहीं?"),
    ("Har jagah notes, kuch bhi organised nahi.", "हर जगह notes, कुछ भी organised नहीं."),
    ("Miliye Lumen Notes se.", "मिलिए Lumen Notes से."),
    ("Poora project ek link mein apni team ke saath share karo.", "पूरा project एक link में अपनी team के साथ share करो."),
])
def test_tts_text_converts_all_hindi_words(text, expected):
    """Regression: these Romanised Hindi words used to reach the voice engine in Latin script."""
    assert H.hindi_words_to_devanagari(text) == expected


def test_english_sentence_is_left_alone():
    s = "Capture any idea in seconds, from any device."
    assert H.hindi_words_to_devanagari(s) == s
    assert D.detect(s)["language"] == "english"


def test_hinglish_keeps_english_loanwords_from_the_source():
    from framefoundry.language.translate import restore_english_loanwords
    out = restore_english_loanwords("सेकंड में किसी भी विचार को कैप्चर करें.", "Capture any idea in seconds.")
    assert out.startswith("Seconds mein") and "capture" in out and "vichaar" in out
    out2 = restore_english_loanwords("एक लिंक में अपनी टीम के साथ पूरी परियोजना साझा करें.", "Share a whole project with your team in one link.")
    assert "link" in out2 and "team" in out2 and "pariyojna" in out2      # a Hindi word is not "restored"
