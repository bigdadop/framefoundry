#!/usr/bin/env python3
"""Script and language detection for Hindi, English and Hinglish narration.

Levels:
  * word      - devanagari | hindi_roman | english | name | number | ambiguous
  * sentence  - hindi | english | hinglish (+ the evidence used)
  * document  - dominant language over all sentences

Rules (deliberately transparent, no model download needed):
  * Devanagari-majority sentences are Hindi (English loanwords in Latin script are normal in
    Hindi copy: "क्या आपकी creativity ...").
  * Roman-script sentences are NOT assumed to be English: Romanised Hindi function words and
    verb forms ("hai", "karo", "mein", "chahiye") are counted; a sentence with enough of them
    is Hinglish.
  * Latin-majority sentences that contain some Devanagari are Hinglish (code-switched).

Usage: python -m framefoundry.language.detect FILE_OR_TEXT [--json]
Allowed language values elsewhere in FrameFoundry: auto, hindi, english, hinglish.
"""
import functools
import json
import os
import re
import sys

from framefoundry.language.hinglish import AMBIGUOUS, LEXICON

LANGUAGES = ("hindi", "english", "hinglish")
ALLOWED = ("auto",) + LANGUAGES

DEV = re.compile(r"[ऀ-ॿ]")
LAT = re.compile(r"[A-Za-z]")
WORD = re.compile(r"[\wऀ-ॿ'’-]+", re.UNICODE)

# ambiguous romanised words that are Hindi when the sentence is already Hindi-leaning
HINDI_LEANING = {"to", "me", "main", "hi", "ki", "ka", "ke", "ye", "ya", "hum", "tum", "ab", "kal", "bas",
                 "sab", "na", "ho", "par", "jab", "tab", "mat", "ham", "do", "de", "le", "lo", "sau", "das",
                 "din", "dil", "sach", "ji", "han", "bhai"}

_EN_CORE = """a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had has have having he
her here hers him his how i if in into is it its just me more most my no nor not now of off on once only or other our
out over own same she should so some such than that the their them then there these they this those through to too
under until up very was we were what when where which while who whom why will with would you your new join win cash
prize prizes event events music song songs create share video videos submit idea ideas real deadline winner winners
open explore opportunities opportunity creativity creative rewards reward exciting today now app website contest
contests section active upcoming completed first second third page generate prompt inspiration theme original rap
hip-hop ai platform free start get make your what if could"""


@functools.lru_cache(maxsize=1)
def english_words():
    """English vocabulary: a core list + every system word list found (+ British spelling variants)."""
    words = set(_EN_CORE.split())
    for p in ("/usr/share/dict/american-english", "/usr/share/dict/british-english", "/usr/share/dict/words",
              "/usr/share/dict/web2"):
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="ignore") as f:
                words.update(w.strip().lower() for w in f if w.strip() and "'" not in w)
    extra = set()
    for w in words:
        if "iz" in w:                                        # organize/organized -> organise/organised
            extra.add(re.sub(r"iz(e|ed|es|ing|ation|ations|er|ers)$", r"is\1", w))
        if w.endswith(("or", "ors", "ored", "oring")) and len(w) > 4:   # color -> colour
            extra.add(re.sub(r"or(s|ed|ing)?$", r"our\1", w))
    words |= extra
    return words


def has_dictionary():
    return len(english_words()) > 5000


def _norm(w):
    return w.lower().strip("'’-")


def _looks_hindi(w):
    """Orthographic hints for unknown Romanised words."""
    return bool(re.search(r"(aa|ee|oo|kh|gh|jh|bh|dh|chh|iye|ein|aon|on)$|^(kh|gh|jh|bh|dh)|aa|ee", w)) \
        and not re.search(r"(tion|ing|ness|ment|able|ible|ous|ity|ism|ize|ise|ed|ly)$", w)


def classify_word(word, context_hindi=False, first=False, strong_hindi=False):
    """first: the word starts a sentence (a capital letter there does not make it a name).
    strong_hindi: the sentence is clearly Hindi-leaning, so an unknown non-English word is Hindi."""
    if DEV.search(word):
        return "devanagari"
    if re.fullmatch(r"[₹$€]?[\d,.]+(%|k|st|nd|rd|th)?", word, re.I):
        return "number"
    w = _norm(word)
    if not w or not LAT.search(w):
        return "other"
    if w in AMBIGUOUS:
        if context_hindi and w in HINDI_LEANING:
            return "hindi_roman"
        return "ambiguous"
    if w in LEXICON:
        return "hindi_roman"
    if w in english_words():
        return "english"
    if word[:1].isupper() and not first:
        return "name"
    if strong_hindi and has_dictionary():      # only trustworthy with a real English word list
        return "hindi_roman"
    return "hindi_roman" if _looks_hindi(w) else "english"


def _context(words):
    """(context_hindi, strong_hindi) from a first, context-free pass over a sentence."""
    first_pass = [classify_word(w, first=(i == 0)) for i, w in enumerate(words)]
    hindi = first_pass.count("hindi_roman") + first_pass.count("devanagari")
    english = first_pass.count("english")
    return hindi > 0, hindi >= 2 and hindi >= 0.5 * english


def classify_sentence(text):
    words = WORD.findall(text)
    counts = {"devanagari": 0, "hindi_roman": 0, "english": 0, "name": 0, "number": 0, "ambiguous": 0, "other": 0}
    ctx, strong = _context(words)
    labels = [classify_word(w, context_hindi=ctx, first=(i == 0), strong_hindi=strong) for i, w in enumerate(words)]
    for lb in labels:
        counts[lb] += 1
    dev, hr, en = counts["devanagari"], counts["hindi_roman"], counts["english"]
    latin = hr + en + counts["ambiguous"] + counts["name"]
    if dev == 0 and latin == 0:
        lang = "unknown"
    elif dev >= latin:
        lang = "hindi"
    elif dev > 0:
        lang = "hinglish"
    elif hr >= 2 and hr / max(1, hr + en) >= 0.2:
        lang = "hinglish"
    elif hr >= 1 and hr / max(1, hr + en) >= 0.34:
        lang = "hinglish"
    else:
        lang = "english"
    return {"text": text, "language": lang, "counts": counts,
            "words": [{"w": w, "type": lb} for w, lb in zip(words, labels)]}


SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")


def split_sentences(text):
    return [s.strip() for s in SENT_SPLIT.split(text) if s and s.strip()]


def _variant(language, counts):
    """Finer label inside a language family (reported, never used to drop words)."""
    dev, hr, en = counts["devanagari"], counts["hindi_roman"], counts["english"]
    latin = hr + en + counts["ambiguous"] + counts["name"]
    if language == "hindi":
        return "devanagari_hindi" if latin <= max(1, dev // 4) else "devanagari_with_english"
    if language == "hinglish":
        if dev and latin:
            return "mixed_script"
        return "romanised_hindi" if en / max(1, hr + en) < 0.34 else "code_mixed"
    return language


def detect(text):
    """Document-level detection.

    Returns {'language': english|hindi|hinglish, 'variant', 'script': latin|devanagari|mixed,
             'scores', 'counts', 'sentences': [{'text', 'language', 'variant', 'words'}]}.
    """
    sents = [classify_sentence(s) for s in split_sentences(text)]
    scores = {k: 0 for k in LANGUAGES}
    total_counts = {"devanagari": 0, "hindi_roman": 0, "english": 0, "name": 0, "number": 0, "ambiguous": 0, "other": 0}
    for s in sents:
        for k, v in s["counts"].items():
            total_counts[k] += v
        if s["language"] in scores:
            n = sum(v for k, v in s["counts"].items() if k not in ("other",))
            scores[s["language"]] += max(1, n)
    total = sum(scores.values()) or 1
    lang = max(scores, key=scores.get) if any(scores.values()) else "english"
    # A mostly-Hindi or mostly-English script with a few Hinglish lines is still Hinglish
    # when those lines are a substantial share (code-switching is the defining feature).
    if lang != "hinglish" and scores["hinglish"] / total >= 0.35:
        lang = "hinglish"
    dev_chars = len(DEV.findall(text)); lat_chars = len(LAT.findall(text))
    script = "devanagari" if dev_chars > 4 * lat_chars else "latin" if lat_chars > 4 * dev_chars else "mixed"
    if not (dev_chars or lat_chars):
        script = "unknown"
    return {"language": lang, "variant": _variant(lang, total_counts), "script": script,
            "scores": {k: round(v / total, 3) for k, v in scores.items()}, "counts": total_counts,
            "sentences": [{"text": s["text"], "language": s["language"], "variant": _variant(s["language"], s["counts"]),
                           "words": s["words"]} for s in sents]}


def compatible(target, detected):
    """Can a script detected as `detected` be narrated as `target` without translation?
    Hindi and Hinglish share the language (only the script/mix differs) - converting between them is
    transliteration, not translation."""
    if target == detected:
        return True
    return {target, detected} <= {"hindi", "hinglish"}


def resolve_language(requested, text):
    """Apply an explicit override or auto-detect."""
    requested = (requested or "auto").lower()
    if requested not in ALLOWED:
        raise ValueError(f"language must be one of {ALLOWED}, got {requested!r}")
    if requested != "auto":
        return requested
    return detect(text)["language"]


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="script file or literal text")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    text = open(a.source, encoding="utf-8").read() if os.path.exists(a.source) else a.source
    res = detect(text)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"language: {res['language']} ({res['variant']}, {res['script']} script)  scores: {res['scores']}")
        for s in res["sentences"]:
            print(f"  [{s['language']:8s} {s['variant']:22s}] {s['text']}")


if __name__ == "__main__":
    sys.exit(main())
