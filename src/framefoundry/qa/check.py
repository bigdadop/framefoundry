"""Final quality assurance of a rendered video (FFprobe + a complete FFmpeg decode).

Errors (the video is NOT marked successful): missing/empty file, wrong codec / size / frame rate, missing or
silent audio, duration not matching the edit, audio/video length mismatch, decode errors (corruption),
unexpected black frames, clipping, subtitles outside the video, referenced assets missing.
Warnings: loudness outside the target window, long frozen footage.
"""
import json
import os
import re

from framefoundry.util import ffmpeg_bin, probe, run


def check(video, edl, expected=None, out_json=None):
    expected = expected or {}
    checks = []

    def add(name, ok, detail, severity="error"):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})
    if not os.path.exists(video) or os.path.getsize(video) < 1000:
        add("file exists", False, "rendered file missing or empty")
        return _finish(checks, video, out_json, {})
    add("file exists", True, f"{os.path.getsize(video) / 1e6:.1f} MB")
    pr = probe(video)
    v = next((s for s in pr.get("streams", []) if s["codec_type"] == "video"), None)
    a = next((s for s in pr.get("streams", []) if s["codec_type"] == "audio"), None)
    dur = float(pr.get("format", {}).get("duration", 0) or 0)
    spec = {}
    if v:
        spec = {"video_codec": v["codec_name"], "width": v["width"], "height": v["height"], "fps": v["r_frame_rate"],
                "pix_fmt": v.get("pix_fmt"), "profile": v.get("profile"), "video_duration": float(v.get("duration", 0) or 0)}
        add("video codec H.264", v["codec_name"] == "h264", v["codec_name"])
        add("resolution", (v["width"], v["height"]) == (edl["w"], edl["h"]),
            f"{v['width']}x{v['height']} (expected {edl['w']}x{edl['h']})")
        add("frame rate", v["r_frame_rate"] == f"{edl['fps']}/1", f"{v['r_frame_rate']} (expected {edl['fps']}/1)")
        add("pixel format", v.get("pix_fmt") == "yuv420p", v.get("pix_fmt"), "warning")
    else:
        add("video stream", False, "no video stream")
    if a:
        spec.update({"audio_codec": a["codec_name"], "sample_rate": int(a.get("sample_rate", 0)), "channels": a.get("channels"),
                     "audio_duration": float(a.get("duration", 0) or 0)})
        add("audio codec AAC", a["codec_name"] == "aac", f"{a['codec_name']} {a.get('sample_rate')} Hz {a.get('channels')} ch")
    else:
        add("audio stream", False, "no audio stream")
    add("duration matches edit", abs(dur - edl["duration"]) < 0.25, f"{dur:.2f}s (edit {edl['duration']:.2f}s)")
    if v and a:
        dv, da = spec["video_duration"], spec["audio_duration"]
        add("audio/video length in sync", abs(dv - da) < 0.1, f"video {dv:.3f}s, audio {da:.3f}s")
        st = [float(s.get("start_time", 0) or 0) for s in (v, a)]
        add("streams start together", abs(st[0] - st[1]) < 0.05, f"start times video {st[0]:.3f}s / audio {st[1]:.3f}s")
    # complete decode: corruption, black frames, freezes, loudness
    r = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-v", "info", "-i", video,
             "-vf", "blackdetect=d=0.1:pix_th=0.06,freezedetect=n=0.0005:d=2.5", "-af", "ebur128=peak=true",
             "-f", "null", "-"], check=False, capture=True)
    log = r.stderr
    errs = [l for l in log.splitlines() if re.search(r"(error|corrupt|invalid|non-existing PPS|decode_slice)", l, re.I)
            and "ebur128" not in l and "Parsed_" not in l]
    add("decodes cleanly", r.returncode == 0 and not errs, "every frame decoded without errors" if not errs else "; ".join(errs[:3]))
    blacks = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    fade = edl.get("fade_out", 0.0)
    bad = [b for b in blacks if b[0] < edl["duration"] - fade - 0.2]
    add("no unexpected black frames", not bad, f"black segments {bad}" if bad else
        "none" + (f" (only the intentional {fade:.2f}s fade-out)" if blacks else ""))
    fr = re.findall(r"freeze_start: ([\d.]+).*?freeze_duration: ([\d.]+)", log, re.S)
    long_fz = [(float(s), float(d)) for s, d in fr if float(d) > 3.0 and float(s) < edl.get("sting_at", 1e9) - 0.5]
    add("no frozen footage", not long_fz, f"static >3 s at {long_fz}" if long_fz else
        f"{len(fr)} short static holds (cards / end card)", "warning")
    I = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", log)
    TP = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", log)
    if I:
        i_val = float(I[-1]) if I[-1] != "-inf" else -99.0
        tp = float(TP[-1]) if TP and TP[-1] != "-inf" else -99.0
        tgt = expected.get("target_lufs", -14.0)
        add("audio not silent", i_val > -40, f"integrated {i_val} LUFS")
        add("loudness", abs(i_val - tgt) <= 1.5, f"integrated {i_val} LUFS (target {tgt})", "warning")
        add("no clipping", tp <= -0.5, f"true peak {tp} dBTP")
        spec.update({"integrated_lufs": i_val, "true_peak_dbtp": tp})
    late = [s for s in edl.get("subtitles", []) if s["end"] > edl["duration"] + 0.01 or s["start"] < 0]
    overlap = sum(1 for x, y in zip(edl.get("subtitles", []), edl.get("subtitles", [])[1:]) if y["start"] < x["end"] - 0.01)
    add("subtitle timing", not late and not overlap,
        f"{len(edl.get('subtitles', []))} events inside the video, none overlapping" if not (late or overlap)
        else f"{len(late)} outside the video, {overlap} overlapping")
    ve = edl.get("voice_end", edl["duration"])
    add("narration fits the video", ve <= edl["duration"] + 0.01, f"narration ends {ve:.2f}s, video {edl['duration']:.2f}s")
    missing = set()

    def walk(o):
        if isinstance(o, dict):
            for k, val in o.items():
                if k in ("src", "icon") and isinstance(val, str):
                    p = val if os.path.isabs(val) else os.path.join(edl.get("root", ""), val)
                    if not os.path.exists(p):
                        missing.add(val)
                else:
                    walk(val)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(edl)
    add("assets present", not missing, "all referenced files exist" if not missing else f"missing: {sorted(missing)[:5]}")
    if expected.get("output_dir"):
        add("output path", os.path.dirname(os.path.abspath(video)) == os.path.abspath(expected["output_dir"]), os.path.abspath(video))
    return _finish(checks, video, out_json, spec, dur)


def _finish(checks, video, out_json, spec, dur=0.0):
    passed = all(c["ok"] for c in checks if c["severity"] == "error")
    res = {"passed": passed, "video": os.path.abspath(video), "duration": round(dur, 3), "spec": spec,
           "errors": [c["name"] for c in checks if not c["ok"] and c["severity"] == "error"],
           "warnings": [c["name"] for c in checks if not c["ok"] and c["severity"] == "warning"], "checks": checks}
    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return res
