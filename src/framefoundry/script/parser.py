"""FrameFoundry script format (plain text or Markdown, UTF-8).

One narration line per row. Everything else is optional:

    ---                                   optional front matter (YAML) with project details
    brand: Acme Studio
    tagline: Make it yours
    website: https://example.com
    ---
    # Hook                                section heading (used for pacing and the timeline)
    Turn your *ideas* into music.         *emphasis* = keyword on cards, highlighted in subtitles
    [web] Open the Events page.           force real website footage for this line
    [web:rec_01@Pricing] ...              a specific recording, starting at its step labelled "Pricing"
    [card] / [flash] / [logo]             kinetic text card / one-word flashes / logo reveal
    [image:product.png] / [video:demo.mp4]  a specific asset from --assets-dir
    Naam hai Acme. {tts: नाम है Acme}     pronunciation text sent to the voice engine only
    // a comment line (ignored)

Markdown bullets ("- ", "* ", "1. ") and block quotes are stripped. Lines are kept in order.
"""
import re
from pathlib import Path

import yaml

from framefoundry.util import FrameFoundryError

TAG = re.compile(r"^\s*\[([a-z]+)(?::([^\]@]+))?(?:@([^\]]+))?\]\s*", re.I)
TTS = re.compile(r"\{\s*tts\s*:\s*(.+?)\}\s*$", re.I)
VISUAL_TAGS = {"web", "card", "flash", "logo", "image", "video"}
FRONT_KEYS = {"title", "brand", "tagline", "website", "handle", "language", "style_profile", "aspect_ratio",
              "call_to_action", "bg_word"}


def read_text(path):
    p = Path(path)
    if not p.is_file():
        raise FrameFoundryError(f"script not found: {path}")
    if p.suffix.lower() not in (".txt", ".md", ".markdown", ".text", ""):
        raise FrameFoundryError(f"unsupported script type '{p.suffix}' (use .txt or .md)")
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FrameFoundryError(f"{path} is not UTF-8 text. Save it as UTF-8 (Hindi needs UTF-8).") from None
    if not text.strip():
        raise FrameFoundryError(f"script {path} is empty")
    return text


def split_front_matter(text):
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise FrameFoundryError(f"invalid front matter in script: {e}") from None
    if not isinstance(meta, dict):
        return {}, text
    unknown = set(meta) - FRONT_KEYS
    meta = {k: v for k, v in meta.items() if k in FRONT_KEYS}
    if unknown:
        meta["_unknown_keys"] = sorted(unknown)
    return meta, text[m.end():]


def parse(text, markdown=False):
    """-> (meta, lines). Each line: {id, text, plain, tts, tag, target, from, section, row}."""
    meta, body = split_front_matter(text)
    lines, section = [], "main"
    for row, raw in enumerate(body.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("//") or s.startswith("<!--") or re.fullmatch(r"[-=_*]{3,}", s):
            continue
        if s.startswith("#"):
            section = re.sub(r"^#+\s*", "", s).strip().lower() or "main"
            continue
        s = re.sub(r"^>\s*", "", s)
        s = re.sub(r"^([-•]|\*(?=\s))\s+", "", s)
        if markdown:
            s = re.sub(r"^\d+[.)]\s+", "", s)
        tag = target = frm = None
        m = TAG.match(s)
        if m and m.group(1).lower() in VISUAL_TAGS:
            tag, target, frm = m.group(1).lower(), (m.group(2) or "").strip() or None, (m.group(3) or "").strip() or None
            s = s[m.end():]
        tts = None
        m2 = TTS.search(s)
        if m2:
            tts = m2.group(1).strip()
            s = s[:m2.start()].strip()
        if not s.strip("*").strip():
            continue
        lines.append({"text": s, "plain": s.replace("*", ""), "tts": tts, "tag": tag, "target": target,
                      "from": frm, "section": section, "row": row})
    for i, L in enumerate(lines, 1):
        L["id"] = f"L{i:02d}"
    return meta, lines


def split_long(lines, max_words=18):
    """Very long lines become two scenes, split at the punctuation closest to the middle."""
    out = []
    for L in lines:
        words = L["text"].split()
        if len(words) <= max_words or L.get("tts"):
            out.append(L)
            continue
        mid = len(words) / 2
        best = None
        for i, w in enumerate(words[:-2]):
            if re.search(r"[,;:.!?—।]$", w) and i >= 3 and (best is None or abs(i - mid) < abs(best - mid)):
                best = i
        cut = (best if best is not None else int(mid)) + 1
        a = dict(L, text=" ".join(words[:cut]))
        b = dict(L, text=" ".join(words[cut:]), tag=L["tag"] if L["tag"] in ("web", "image", "video") else None)
        for d in (a, b):
            d["plain"] = d["text"].replace("*", "")
        out += [a, b]
    for i, L in enumerate(out, 1):
        L["id"] = f"L{i:02d}"
    return out


def load_script(path):
    text = read_text(path)
    meta, lines = parse(text, markdown=Path(path).suffix.lower() in (".md", ".markdown"))
    if not lines:
        raise FrameFoundryError(f"no narration lines found in {path}")
    return meta, lines, text


def find_variant(script_path, language):
    """script.txt + 'hindi' -> script.hindi.txt / script.hi.txt (same folder) if it exists."""
    p = Path(script_path)
    short = {"english": "en", "hindi": "hi", "hinglish": "hinglish"}[language]
    for name in (f"{p.stem}.{language}{p.suffix}", f"{p.stem}.{short}{p.suffix}", f"{p.stem}_{language}{p.suffix}"):
        q = p.with_name(name)
        if q.is_file() and q != p:
            return q
    return None


def lines_to_text(lines, meta=None):
    """Write lines back to the script format (used to save adapted language versions)."""
    out = []
    if meta:
        clean = {k: v for k, v in meta.items() if not k.startswith("_")}
        if clean:
            out += ["---", yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip(), "---"]
    section = None
    for L in lines:
        if L.get("section") and L["section"] != section:
            section = L["section"]
            if section != "main":
                out.append(f"# {section.title()}")
        tag = ""
        if L.get("tag"):
            tag = f"[{L['tag']}" + (f":{L['target']}" if L.get("target") else "") + (f"@{L['from']}" if L.get("from") else "") + "] "
        tts = f" {{tts: {L['tts']}}}" if L.get("tts") else ""
        out.append(f"{tag}{L['text']}{tts}")
    return "\n".join(out) + "\n"
