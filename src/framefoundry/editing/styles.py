"""Reusable style profiles (editing grammar + palette + fonts + pacing).

Built-in profiles live in templates/profiles/*.yaml; your own (including ones generated from reference
videos and private brand profiles) live in templates/profiles/local/ — that folder is git-ignored.
A profile only needs the keys it changes: everything else comes from BASE_PROFILE.
"""
import copy
import json
import re
from pathlib import Path

import yaml

from framefoundry.config import get_config
from framefoundry.util import FrameFoundryError

BASE_PROFILE = {
    "name": "base",
    "description": "",
    "palette": {
        "cards": [{"bg": "#F7F5F0", "word": "#ECE7DC"}, {"bg": "#EEF3FA", "word": "#DCE5F2"},
                  {"bg": "#FFF1E6", "word": "#F8E0CC"}, {"bg": "#F2F0FF", "word": "#E2DEFA"}],
        "accents": ["#2563EB", "#DB2777", "#EA580C", "#7C3AED"],
        "ink": "#111827", "alert": "#E0251B", "end_bg": "#0F172A", "end_word": "#18223A",
        "glow": "#6366F1", "tagline": "#F472B6",
    },
    "fonts": {"display": "oswald", "display_bold": "oswald-bold", "display_med": "oswald-med", "body": "inter",
              "body_semi": "inter-semi", "bg_word": "bebas", "subtitle": "inter-semi", "subtitle_emph": "inter-italic",
              "brand": "bebas"},
    "cards": {"bg_word": True, "max_words": 5, "keyword_height": 0.34, "small_height": 0.105, "stickers": True,
              "bang_on_punct": True, "slam_emphasis": True, "grid": False},
    "subtitles": {"enabled": True, "on_cards": False, "size": 0.0556, "y": 0.896, "min_words": 2, "max_words": 4,
                  "color": "#FFFFFF", "shadow": 0.62, "emph_glow": 0.55, "box": None, "karaoke": False,
                  "highlight_color": None},
    "transitions": {"web_web": "whip", "card_web": "cut", "web_card": "cut", "card_card": "cut", "to_end": "cut",
                    "duration": 0.22, "fade_duration": 0.4, "alternate": True},
    "camera": {"zoom_min": 1.3, "zoom_base": 2.0, "zoom_max": 2.7, "follow_cursor": True, "punch_on_emphasis": True,
               "max_speed": 2.4, "min_speed": 0.45, "grade": {"contrast": 1.04, "sat": 1.06}},
    "structure": {"hook_lines": 3, "cta_lines": 2, "reveal": True, "flash_last_line": True, "flash_max_words": 3,
                  "end_card": True, "end_hold": 3.6, "sting_pad": 0.34, "sticker_every_web": 3},
    "rhythm": {"words_per_second": 3.0, "line_gap": 0.12, "section_gap": 0.24, "max_shot": 4.0},
    "watermark": {"enabled": True, "card": "card", "web": "corner"},
    "endcard": {"slow_zoom": 0.035},
    "music": {"bed_db": -5.0, "duck": True, "fade_in": 0.03, "fade_out": 1.5, "target_lufs": -14},
    "voice": {"speed": 1.0, "lead_in": 0.15},
    "output": {"aspect": "16:9", "fps": 30, "fade_out": 0.45},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def profile_dirs():
    base = get_config().path("profiles")
    return [base / "local", base]


def _read(path):
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise FrameFoundryError(f"style profile {path} is not a mapping")
    return data


def list_profiles():
    seen, out = set(), []
    for d in profile_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix in (".yaml", ".yml", ".json") and p.stem not in seen:
                seen.add(p.stem)
                try:
                    data = _read(p)
                except Exception as e:  # noqa: BLE001 - report broken files instead of crashing a listing
                    data = {"description": f"(unreadable: {e})"}
                out.append({"name": p.stem, "path": str(p), "local": d.name == "local",
                            "description": (data.get("description") or "")[:120]})
    return out


def find_profile(name_or_path):
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml", ".json") and p.is_file():
        return p
    for d in profile_dirs():
        for ext in (".yaml", ".yml", ".json"):
            q = d / f"{name_or_path}{ext}"
            if q.is_file():
                return q
    names = ", ".join(x["name"] for x in list_profiles()) or "none"
    raise FrameFoundryError(f"style profile '{name_or_path}' not found (available: {names})")


def load_profile(name_or_path=None):
    name_or_path = name_or_path or get_config().main["style"]["default_profile"]
    path = find_profile(name_or_path)
    prof = _merge(BASE_PROFILE, _read(path))
    prof.setdefault("name", path.stem)
    prof["_path"] = str(path)
    return prof


def save_profile(profile, name, overwrite=False):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name):
        raise FrameFoundryError("profile names may only contain letters, digits, '-' and '_'")
    d = profile_dirs()[0]
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{name}.yaml"
    if out.exists() and not overwrite:
        raise FrameFoundryError(f"profile {out} exists (use --overwrite)")
    data = {k: v for k, v in profile.items() if not k.startswith("_")}
    data["name"] = name
    out.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out
