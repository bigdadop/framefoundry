"""scene_timeline.md — a human-readable edit list (what is on screen, when, and why)."""


def _ts(t):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:05.2f}"


def write(path, edl, scenes, timing, title, language, notes=()):
    by = {s["id"]: s for s in scenes}
    trans = {round(t["at"], 2): t for t in edl.get("transitions", [])}
    md = [f"# Scene timeline — {title}", "",
          f"Language **{language}** · duration **{edl['duration']:.2f} s** · {edl['w']}×{edl['h']} · {edl['fps']} fps · "
          f"narration ends {_ts(edl.get('voice_end', 0))} · end card at {_ts(edl.get('sting_at', edl['duration']))}", "",
          "| Time | Scene | Section | Narration | Visual | Transition in |", "|---|---|---|---|---|---|"]
    seen = set()
    for cl in edl["clips"]:
        sid = cl.get("scene", "")
        s = by.get(sid, {})
        first = sid not in seen
        seen.add(sid)
        narr = (s.get("subtitle") or s.get("narration") or "").replace("|", "/") if first else "〃"
        t = trans.get(round(cl["start"], 2))
        tr = (t["kind"] + (f" {t['dir']}" if t.get("dir") else "")) if t else "cut"
        md.append(f"| {_ts(cl['start'])}–{_ts(cl['end'])} | {sid} | {s.get('section', '') if first else ''} | {narr} | "
                  f"{cl.get('desc', cl['type'])} | {tr} |")
    methods = {}
    for v in timing.values():
        methods[v.get("words_method", "?")] = methods.get(v.get("words_method", "?"), 0) + 1
    md += ["", f"Subtitle events: {len(edl.get('subtitles', []))} (burned in; see subtitles.srt).",
           f"Word timing: {', '.join(f'{n} line(s) {k}' for k, n in methods.items())}.",
           "Website visuals are real Playwright screen recordings or your uploaded recordings — never mock-ups."]
    if notes:
        md += ["", "## Notes", *[f"- {n}" for n in notes]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
