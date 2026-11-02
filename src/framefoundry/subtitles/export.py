"""Subtitle files (SRT / WebVTT) from the EDL's subtitle events and the narration timing.

Two flavours are written:
  subtitles.srt / .vtt          phrase-level events exactly as burned into the video (footage scenes)
  subtitles_full.srt            one event per narration line (for platforms that add their own captions)
"""
import os


def ts(t, sep=","):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _write(events, base):
    srt, vtt = [], ["WEBVTT", ""]
    for i, (a, b, text) in enumerate(events, 1):
        srt += [str(i), f"{ts(a)} --> {ts(b)}", text, ""]
        vtt += [f"{ts(a, '.')} --> {ts(b, '.')}", text, ""]
    with open(base + ".srt", "w", encoding="utf-8") as f:
        f.write("\n".join(srt))
    with open(base + ".vtt", "w", encoding="utf-8") as f:
        f.write("\n".join(vtt))


def write_all(edl, timing, scenes, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    burned = [(s["start"], s["end"], " ".join(w[0] for w in s["words"])) for s in edl.get("subtitles", [])]
    _write(burned, os.path.join(out_dir, "subtitles"))
    full = []
    by = {s["id"]: s for s in scenes}
    for lid, t in sorted(timing.items(), key=lambda kv: kv[1]["start"]):
        text = (by.get(lid, {}).get("subtitle") or by.get(lid, {}).get("narration") or "").replace("*", "")
        if text:
            full.append((t["start"], t["end"], text))
    srt = []
    for i, (a, b, text) in enumerate(full, 1):
        srt += [str(i), f"{ts(a)} --> {ts(b)}", text, ""]
    with open(os.path.join(out_dir, "subtitles_full.srt"), "w", encoding="utf-8") as f:
        f.write("\n".join(srt))
    return {"burned_events": len(burned), "line_events": len(full)}
