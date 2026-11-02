"""Script lines -> scene plan (what is on screen while each line is spoken).

Editing grammar (from the SunoSunao promo, generalised and driven by the style profile):
  * hook lines (opening)      -> kinetic-typography cards; very short lines become flash words
  * product / feature lines   -> real website recordings, uploaded screen recordings, videos or images
  * a line naming the brand    -> logo reveal (when a logo is available and no footage is used for it)
  * call-to-action lines       -> cards; the final short line becomes flash words
Explicit [tags] in the script always win. Scene durations come later from the narration timing,
never from an equal split.
"""
import re


def _core(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match_asset(assets, name):
    if not name:
        return None
    n = name.lower()
    return next((a for a in assets if a["name"].lower() == n), None) or \
        next((a for a in assets if n in a["name"].lower()), None)


def plan_scenes(lines, ctx):
    """ctx: {style, brand: {name, logo}, recordings: [{name, title, steps}], images: [{id, name}],
    videos: [{id, name}]} -> [scene]"""
    style = ctx["style"]
    st = style.get("structure", {})
    n = len(lines)
    if not n:
        return []
    recs = ctx.get("recordings") or []
    images = ctx.get("images") or []
    videos = ctx.get("videos") or []
    has_logo = bool((ctx.get("brand") or {}).get("logo"))
    footage = bool(recs)
    named_sections = any(L["section"] != "main" for L in lines)
    hook_n = min(st.get("hook_lines", 3), max(0, n // 3)) if n >= 4 else (1 if n >= 3 else 0)
    cta_n = min(st.get("cta_lines", 2), max(0, (n - hook_n) // 3)) if n >= 4 else 0
    brand_core = _core((ctx.get("brand") or {}).get("name", ""))
    scenes, img_i, vid_i, body_i = [], 0, 0, 0
    revealed = False
    for i, L in enumerate(lines):
        words = L["plain"].split()
        if named_sections:
            sec = L["section"]
            role = "hook" if re.search(r"hook|intro|open", sec) and i < n / 2 else \
                   "cta" if re.search(r"cta|call|clos|end|outro", sec) else "body"
        else:
            sec = "hook" if i < hook_n else "cta" if i >= n - cta_n else "body"
            role = sec
        tag = L.get("tag")
        if tag in ("card", "flash", "logo"):
            vis = {"type": tag}
        elif tag == "web":
            vis = {"type": "web", "recording": L.get("target"), "from": L.get("from")}
        elif tag == "image":
            a = _match_asset(images, L.get("target")) or (images[img_i % len(images)] if images else None)
            vis = {"type": "image", "asset_id": a["id"]} if a else {"type": "card"}
            img_i += 1
        elif tag == "video":
            a = _match_asset(videos, L.get("target")) or (videos[vid_i % len(videos)] if videos else None)
            vis = {"type": "video", "asset_id": a["id"]} if a else {"type": "card"}
            vid_i += 1
        elif role == "hook":
            vis = {"type": "flash" if 0 < len(words) <= st.get("flash_max_words", 3) and i > 0 else "card"}
        elif role == "cta":
            last = i == n - 1
            vis = {"type": "flash" if last and st.get("flash_last_line", True) and len(words) <= 4 else "card"}
        else:
            mentions_brand = brand_core and brand_core in _core(L["plain"]) and len(words) <= 8
            if mentions_brand and has_logo and not revealed and st.get("reveal", True):
                vis = {"type": "logo"}
                revealed = True
            elif footage:
                vis = {"type": "web"}
            elif videos and (body_i % 2 == 0 or not images):
                vis = {"type": "video", "asset_id": videos[vid_i % len(videos)]["id"]}
                vid_i += 1
            elif images:
                vis = {"type": "image", "asset_id": images[img_i % len(images)]["id"]}
                img_i += 1
            else:
                vis = {"type": "card"}
            body_i += 1
        scenes.append({"id": L["id"], "section": sec, "role": role, "narration": L["plain"], "subtitle": L["text"],
                       "tts": L.get("tts"), "visual": vis, "keyword": None, "sticker": True})
    # spread website scenes over the recordings in order
    webs = [s for s in scenes if s["visual"]["type"] == "web"]
    if recs and webs:
        k, R = len(webs), len(recs)
        for j, s in enumerate(webs):
            if not s["visual"].get("recording"):
                s["visual"]["recording"] = recs[min(R - 1, j * R // k)]["name"]
    every = st.get("sticker_every_web", 3)
    for j, s in enumerate(webs):
        s["sticker_overlay"] = bool(every and j % every == every - 1)
    return scenes


def estimate_timeline(scenes, wps=3.0, gap=0.14):
    t, rows = 0.15, []
    for s in scenes:
        d = max(0.8, len((s.get("narration") or "").split()) / max(1.5, wps))
        rows.append({"id": s["id"], "start": round(t, 2), "end": round(t + d, 2)})
        t += d + gap
    return rows, t
