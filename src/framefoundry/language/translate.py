"""Create a language version of a script — only when one is explicitly requested
(--all-languages, or --language X for a script written in another language).

Priority for every target language:
  1. a variant the user supplied (script.<language>.txt next to the script, or --script-<language>)
  2. the source itself when it is already in that language
  3. transliteration between Hindi (Devanagari) and Hinglish (Latin script) — same language, so no
     meaning changes: hindi -> hinglish romanises; hinglish -> hindi writes the Hindi words in Devanagari
  4. local machine translation (Helsinki-NLP opus-mt via transformers, Apache-2.0) — needs the ML
     extras; the result is marked "machine_translated" and should be reviewed before publishing

Structure (sections, visual tags, line order) is preserved; *emphasis* marks and {tts: …} overrides
are dropped from translated lines because they no longer correspond to the new wording.
"""
import functools
import re

from framefoundry.language import hinglish as H
from framefoundry.util import FrameFoundryError, info

PROTECT = re.compile(r"(https?://\S+|\b[\w-]+\.(?:ai|com|in|io|org|net|app|co)\b|\b[A-Z][a-z]+[A-Z]\w*\b|\b[A-Z]{2,}\b|₹\s?[\d,]+)")


def _plain(text):
    return text.replace("*", "")


@functools.lru_cache(maxsize=4)
def _marian(model_name):
    import importlib.util
    try:
        if importlib.util.find_spec("torch") is None:
            raise ImportError("torch")
        from transformers import MarianMTModel, MarianTokenizer
    except ImportError:
        raise FrameFoundryError("machine translation needs the ML extras (./install.sh without --minimal). "
                                "Alternatively supply the language version yourself as script.<language>.txt") from None
    try:            # offline first
        tok = MarianTokenizer.from_pretrained(model_name, local_files_only=True)
        model = MarianMTModel.from_pretrained(model_name, local_files_only=True)
        info(f"translation model {model_name} loaded from models/")
    except Exception:  # noqa: BLE001 - not cached (transformers raises OSError or TypeError depending on the file)
        info(f"downloading translation model {model_name} into models/ (first use only)")
        tok = MarianTokenizer.from_pretrained(model_name)
        model = MarianMTModel.from_pretrained(model_name)
    model.eval()
    return tok, model


SENT = re.compile(r"(?<=[.!?।])\s+")
# two or more capitalised words in a row inside a sentence ("Lumen Notes", "Gully Cypher") = a name
MULTI_CAP = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


def machine_translate(texts, src, dst, models, protect=()):
    """Translate strings with opus-mt, sentence by sentence (the model drops later sentences of a
    multi-sentence input). Brand names / given terms, URLs, ALLCAPS, CamelCase words, multi-word names
    and prices are replaced by placeholders so the model cannot mangle them."""
    key = f"{src}-{dst}"
    name = models.get(key)
    if not name:
        raise FrameFoundryError(f"no translation model configured for {key} (language_config.yaml: translation.models)")
    tok, model = _marian(name)
    import torch
    terms = sorted({t for t in protect if t and len(t) > 1}, key=len, reverse=True)
    term_re = re.compile("|".join(re.escape(t) for t in terms)) if terms else None
    out = []
    for text in texts:
        parts = []
        for sent in [x for x in SENT.split(text.strip()) if x]:
            prot = {}

            def sub(m):
                k = f"ZX{len(prot)}Q"
                prot[k] = m.group(0)
                return k
            masked = term_re.sub(sub, sent) if term_re else sent
            masked = PROTECT.sub(sub, masked)
            masked = MULTI_CAP.sub(sub, masked)
            batch = tok([masked], return_tensors="pt", padding=True, truncation=True, max_length=256)
            with torch.inference_mode():
                gen = model.generate(**batch, num_beams=4)
            res = tok.batch_decode(gen, skip_special_tokens=True)[0]
            for k, v in prot.items():
                res = res.replace(k, v) if k in res else (res if v in res else f"{v} {res}")
            parts.append(res.strip())
        out.append(" ".join(parts))
    return out


_EN_SOUND = [(r"tion", "shn"), (r"sion", "shn"), (r"ture", "Cr"), (r"ch", "C"), (r"ck", "k"), (r"c(?=[eiy])", "s"), (r"c", "k"),
             (r"ph", "f"), (r"qu", "kw"), (r"x", "ks"), (r"wh", "w"), (r"^kn", "n"), (r"^wr", "r"), (r"gh", ""),
             (r"mb$", "m"), (r"(?<=.)e$", ""), (r"(?<=[^s])s$", ""), (r"es$", "")]


def _skeleton_en(word):
    """Approximate consonant skeleton of an English word as an Indian speaker would say it."""
    w = word.lower()
    for a, b in _EN_SOUND:
        w = re.sub(a, b, w)
    w = re.sub(r"[^a-zC]", "", w).replace("C", "c")        # C = the "ch" sound, kept apart from c -> k
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiouyw]|(?<=[bcdgjklmnprstvz])h", "", w))


def _skeleton_dev(word):
    w = H.devanagari_to_roman(word).lower()
    w = w.replace("ph", "f")
    w = re.sub(r"(?<=[^s])s$", "", w)
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]|[aeiouyw]|(?<=[bcdgjklmnprstvz])h", "", w))


def restore_english_loanwords(dev_text, english_source):
    """Hinglish keeps English nouns in English: a Devanagari word that sounds like a word of the English
    source sentence ("नोट्स" ~ notes, "कैप्चर" ~ capture, "फ़ोल्डर" ~ folders) is written in English;
    everything else is romanised. Only words of the same source sentence are candidates."""
    from framefoundry.audio.align import _sim
    src = [(w, _skeleton_en(w)) for w in re.findall(r"[A-Za-z][A-Za-z'-]*", english_source) if len(w) > 2]
    out = []
    for w in dev_text.split():
        core = re.sub(r"[^\u0900-\u097F]", "", w)
        trail = w[len(w.rstrip(".,!?;:।")):].replace("।", ".")
        pick = None
        if core:
            sk = _skeleton_dev(core)
            for ew, esk in src:
                if len(sk) >= 2 and (sk == esk or (len(sk) >= 4 and _sim(sk, esk) >= 0.8)):
                    pick = ew.lower()
                    break
        out.append(pick + trail if pick else H.devanagari_to_roman(w))
    text = " ".join(out)
    text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)   # sentence case
    return text[:1].upper() + text[1:] if text else text


def _lines_with(lines, texts, tts=None):
    new = []
    for i, (L, t) in enumerate(zip(lines, texts)):
        d = dict(L)
        d["text"] = t
        d["plain"] = _plain(t)
        d["tts"] = tts[i] if tts else None
        new.append(d)
    return new


def adapt(lines, source, target, cfg_language, protect=()):
    """lines: parsed script lines; returns (new_lines, method, notes). method: source |
    transliteration | machine_translation. protect: brand names / terms that must not be translated."""
    if source == target:
        return lines, "source", []
    models = cfg_language["translation"]["models"]
    backend = cfg_language["translation"].get("backend", "marian")
    plain = [_plain(L["text"]) for L in lines]
    if source == "hindi" and target == "hinglish":
        roman = [H.transliterate_text(t, "roman") for t in plain]
        # the original Devanagari is the better TTS input
        return _lines_with(lines, roman, tts=[L.get("tts") or _plain(L["text"]) for L in lines]), "transliteration", \
            ["Hinglish version = the Hindi script romanised (Devanagari -> casual Roman spelling)."]
    if source == "hinglish" and target == "hindi":
        dev = [H.hindi_words_to_devanagari(t) for t in plain]
        return _lines_with(lines, dev), "transliteration", \
            ["Hindi version = the Hinglish script with its Hindi words written in Devanagari (English words kept)."]
    if backend in (None, "none"):
        raise FrameFoundryError(f"a {target} version is needed but translation is disabled; supply script.{target}.txt")
    notes = ["MACHINE TRANSLATED with a local opus-mt model — review the wording before publishing."]
    if source == "english" and target == "hindi":
        return _lines_with(lines, machine_translate(plain, "english", "hindi", models, protect)), "machine_translation", notes
    if source == "english" and target == "hinglish":
        hi = machine_translate(plain, "english", "hindi", models, protect)
        roman = [restore_english_loanwords(h, en) for h, en in zip(hi, plain)]
        return _lines_with(lines, roman, tts=hi), "machine_translation", \
            notes + ["Hinglish version = machine-translated Hindi in Roman script; words that match the English source "
                     "were kept in English."]
    if source == "hindi" and target == "english":
        return _lines_with(lines, machine_translate(plain, "hindi", "english", models, protect)), "machine_translation", notes
    if source == "hinglish" and target == "english":
        dev = [H.hindi_words_to_devanagari(t) for t in plain]
        return _lines_with(lines, machine_translate(dev, "hindi", "english", models, protect)), "machine_translation", notes
    raise FrameFoundryError(f"cannot create a {target} version from a {source} script")
