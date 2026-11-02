#!/usr/bin/env python3
"""Combine per-video reference analyses (+ an optional base template) into one reusable style profile.

* near-duplicate references (same edit re-encoded / re-shared) are detected from the cut list + audio
  envelope and counted once
* measured values override the base template only where the measurement is confident

Used by: ./framefoundry.sh analyze-reference VIDEO [...] --save-profile NAME
"""
import copy

import numpy as np

def similar(a, b):
    """Near-duplicate test between two analyses."""
    da, db = a["spec"]["duration"], b["spec"]["duration"]
    if abs(da - db) > max(1.0, 0.03 * max(da, db)): return 0.0
    ca, cb = a.get("cuts", []), b.get("cuts", [])
    match = sum(1 for x in ca if any(abs(x - y) < 0.25 for y in cb)) / max(1, max(len(ca), len(cb)))
    ea, eb = (a.get("audio") or {}).get("envelope"), (b.get("audio") or {}).get("envelope")
    corr = 0.0
    if ea and eb:
        n = min(len(ea), len(eb))
        if n > 20: corr = float(np.corrcoef(ea[:n], eb[:n])[0, 1])
    return max(match, corr) if (match > 0.6 or corr > 0.85) else min(match, corr)

def lighten(hexc, k):
    c = [int(hexc[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(int(x + (255 - x) * k) for x in c)

def build(analyses, names, base):
    """analyses: [analysis dicts], names: [str], base: full profile dict -> new profile dict"""
    A = list(analyses)
    # ---- duplicates
    dup_of = {}
    for i in range(len(A)):
        for j in range(i):
            if j not in dup_of and similar(A[i], A[j]) > 0.8:
                dup_of[i] = j; break
    uniq = [i for i in range(len(A)) if i not in dup_of]
    U = [A[i] for i in uniq]
    prof = copy.deepcopy(base)
    prof.pop("builtin", None)
    notes, measured = [], {}
    # ---- palette
    cards = [c for x in U for c in x["cards"]["palette"]]
    if cards:
        merged = []
        for c in sorted(cards, key=lambda c: -c["weight"]):
            if not any(sum(abs(int(c["bg"][k:k + 2], 16) - int(m["bg"][k:k + 2], 16)) for k in (1, 3, 5)) < 40 for m in merged):
                merged.append(c)
        if len(merged) >= 2:
            prof["palette"]["cards"] = [{"bg": c["bg"], "word": c["word"]} for c in merged[:4]]
            notes.append(f"card colours measured from references: {', '.join(c['bg'] for c in merged[:4])}")
        elif len(merged) == 1:
            prof["palette"]["cards"] = [{"bg": merged[0]["bg"], "word": merged[0]["word"]}] + prof["palette"]["cards"][1:]
            notes.append(f"primary card colour {merged[0]['bg']} (others kept from base template)")
    acc = [c for x in U for c in x["cards"]["accents"][:3]]
    if len(acc) >= 2:
        uniq_acc = []
        for c in acc:
            if not any(sum(abs(int(c[k:k + 2], 16) - int(u[k:k + 2], 16)) for k in (1, 3, 5)) < 60 for u in uniq_acc): uniq_acc.append(c)
        prof["palette"]["accents"] = uniq_acc[:4] + prof["palette"]["accents"][len(uniq_acc[:4]):4]
        notes.append(f"accent colours: {', '.join(uniq_acc[:4])}")
    inks = [x["cards"]["ink"] for x in U if x["cards"].get("ink")]
    if inks: prof["palette"]["ink"] = inks[0]
    ends = [x["cards"]["dark_cards"][-1] for x in U if x["cards"].get("ends_on_dark_card") and x["cards"]["dark_cards"]]
    if ends:
        prof["palette"]["end_bg"] = ends[0]; prof["palette"]["end_word"] = lighten(ends[0], 0.06)
        prof["structure"]["end_card"] = True
        notes.append(f"ends on a dark end card ({ends[0]})")
    # ---- rhythm / cards
    shots = [s for x in U for s in x.get("shots", [])]
    if shots:
        med = float(np.median(shots))
        prof["rhythm"]["max_shot"] = round(float(np.clip(np.percentile(shots, 75), 1.5, 6.0)), 2)
        measured["median_shot_s"] = round(med, 2)
    cpm = [x["shot_stats"]["cuts_per_min"] for x in U]
    measured["cuts_per_min"] = round(float(np.mean(cpm)), 1) if cpm else None
    wps = [x["audio"]["narration"]["words_per_second"] for x in U
           if (x.get("audio") or {}).get("narration", {}).get("words_per_second")]
    if wps:
        w = float(np.median(wps)); measured["narrator_wps"] = round(w, 2)
        prof["rhythm"]["words_per_second"] = round(w, 2)
        prof["voice"]["speed"] = round(float(np.clip(w / 3.1, 0.9, 1.15)), 2)
        notes.append(f"narration pace {w:.2f} words/s -> TTS speed {prof['voice']['speed']}")
    card_ratio = float(np.mean([x["cards"]["ratio"] for x in U]))
    measured["card_ratio"] = round(card_ratio, 2)
    typo_open = [x for x in U if x["cards"].get("opening_is_typographic")]
    if typo_open:
        ff = [x["cards"]["first_footage_at"] for x in typo_open if x["cards"].get("first_footage_at")]
        hook_s = float(np.median(ff)) if ff else 6.0
        prof["structure"]["hook_lines"] = int(np.clip(round(hook_s / 2.6), 1, 4))
        measured["typographic_hook_s"] = round(hook_s, 1)
        notes.append(f"opens with a {hook_s:.1f}s typographic hook -> {prof['structure']['hook_lines']} hook card lines")
    elif card_ratio < 0.08:
        prof["structure"]["hook_lines"] = 0
        notes.append("references open on footage (no typographic hook)")
    # ---- transitions
    mix = {}
    for x in U:
        for k, v in x["transition_mix"].items(): mix[k] = mix.get(k, 0) + v
    total = sum(mix.values()) or 1
    whips = mix.get("whip_v", 0) + mix.get("whip_h", 0)
    if whips / total > 0.12: prof["transitions"]["web_web"] = "whip"
    elif mix.get("dissolve_or_motion", 0) / total > 0.3: prof["transitions"]["web_web"] = "punch"
    elif total > 3 and whips == 0: prof["transitions"]["web_web"] = "cut"
    measured["transition_mix"] = mix
    # ---- camera
    mv = [x["camera"]["moving_ratio"] for x in U if x["camera"]["pairs"] >= 5]
    if mv:
        m = float(np.mean(mv)); measured["camera_moving_ratio"] = round(m, 2)
        if m > 0.5: prof["camera"]["zoom_base"] = max(prof["camera"].get("zoom_base", 1.8), 1.9)
        elif m < 0.15: prof["camera"]["zoom_base"] = 1.35; prof["camera"]["punch_on_emphasis"] = False
        notes.append(f"footage camera moving {m:.0%} of the time")
    # ---- subtitles
    sd = [x["subtitles"] for x in U if x["subtitles"]["footage_frames"] >= 4]
    if sd:
        det = [s for s in sd if s["detected"]]
        if det:
            prof["subtitles"]["enabled"] = True
            ys = [s["y"] for s in det if s["y"]]
            if ys: prof["subtitles"]["y"] = round(float(np.clip(np.median(ys) + 0.04, 0.7, 0.93)), 3)
            notes.append(f"burned-in subtitles detected at ~{prof['subtitles']['y']:.0%} of frame height")
        elif len(sd) == len(U):
            prof["subtitles"]["enabled"] = False
            notes.append("no burned-in subtitles detected")
    # ---- music
    bpm = [x["audio"]["tempo_bpm"] for x in U if (x.get("audio") or {}).get("tempo_bpm")]
    if bpm: measured["music_bpm"] = round(float(np.median(bpm)), 1)
    en = [x["audio"]["energy"] for x in U if (x.get("audio") or {}).get("energy")]
    if en: measured["music_energy"] = max(set(en), key=en.count)
    langs = [x["audio"]["narration"]["language"] for x in U if (x.get("audio") or {}).get("narration", {}).get("language")]
    if langs: measured["narration_language"] = max(set(langs), key=langs.count)
    prof["analysis"] = {"references": [{"name": names[i] if i < len(names) else str(i), "duplicate_of": (names[dup_of[i]] if i in dup_of else None),
                                        "duration": A[i]["spec"]["duration"], "cuts": len(A[i]["cuts"])} for i in range(len(A))],
                        "unique_references": len(U), "measured": measured, "notes": notes,
                        "based_on_template": base.get("name")}
    prof.pop("_path", None)
    ref_names = [names[i] if i < len(names) else str(i) for i in uniq]
    prof["description"] = (f"Generated from {len(U)} reference video(s) ({', '.join(ref_names)[:80]}) "
                           f"on top of '{base.get('name', 'base')}'")
    return prof
