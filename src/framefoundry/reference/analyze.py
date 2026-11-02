#!/usr/bin/env python3
"""Measure one reference video's editing style (all local: FFmpeg + OpenCV + librosa [+ faster-whisper]).

Only general editing characteristics are extracted (pace, cuts, transitions, palette, camera motion,
subtitle placement, music tempo, narration pace). No footage, branding or audio is copied.

Measures: spec, scene cuts + shot lengths, transition type at each cut (hard cut vs vertical/horizontal
whip-slide), flat "text card" frames vs footage, card background colours + faded-word tint, accent / ink
colours, camera motion & zoom in footage, burned-in subtitle band (position / size), footage theme,
music tempo / energy / brightness, narration pace (words/s) and language, a contact sheet, and a
fingerprint (cut list + audio envelope) for near-duplicate detection.

Writes OUT_DIR/analysis.json and OUT_DIR/contact.jpg
"""
import json
import os
import subprocess

import cv2
import numpy as np

from framefoundry.util import ffmpeg_bin, ffprobe_bin
from framefoundry.util import progress as _progress

FFMPEG = None
FFPROBE = None


def progress(x, msg):
    _progress(x, msg)

def probe(path):
    pr = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                                   capture_output=True, text=True).stdout or "{}")
    v = next((s for s in pr.get("streams", []) if s["codec_type"] == "video"), None)
    au = next((s for s in pr.get("streams", []) if s["codec_type"] == "audio"), None)
    if not v: raise SystemExit("no video stream found")
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return {"width": int(v["width"]), "height": int(v["height"]), "fps": round(fps, 2), "codec": v.get("codec_name"),
            "duration": float(pr["format"].get("duration", 0)), "has_audio": bool(au)}

def frames(path, fps, w, h):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", path, "-vf", f"fps={fps},scale={w}:{h}:flags=area",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    return np.frombuffer(raw[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)

def hexc(c): return "#%02X%02X%02X" % tuple(int(max(0, min(255, x))) for x in c)

def dominant(frame, q=12):
    f = (frame // q).reshape(-1, 3)
    keys, counts = np.unique(f[:, 0].astype(np.int32) * 10000 + f[:, 1] * 100 + f[:, 2], return_counts=True)
    k = keys[np.argmax(counts)]
    col = np.array([k // 10000, (k // 100) % 100, k % 100]) * q + q / 2
    d = np.abs(frame.reshape(-1, 3).astype(np.int16) - col).max(1)
    return col, float((d < 22).mean())

def cluster_colors(cols, weights, tol=26, k=4):
    out = []
    for c, w in sorted(zip(cols, weights), key=lambda x: -x[1]):
        for o in out:
            if np.abs(o[0] - c).max() < tol: o[1] += w; break
        else: out.append([np.array(c, float), w])
    out.sort(key=lambda x: -x[1])
    return [(o[0], o[1]) for o in out[:k]]

class _Args:
    pass


def analyze(video, out_dir, cfg=None, transcribe=True):
    """-> analysis dict (also written to out_dir/analysis.json)."""
    global FFMPEG, FFPROBE
    FFMPEG, FFPROBE = ffmpeg_bin(), ffprobe_bin()
    a = _Args()
    a.video, a.out, a.whisper = str(video), str(out_dir), ("yes" if transcribe and cfg is not None else "none")
    os.makedirs(a.out, exist_ok=True)
    progress(0.02, "probing")
    spec = probe(a.video)
    ar = spec["width"] / spec["height"]
    sw = 192; sh = max(2, int(round(sw / ar / 2)) * 2)
    progress(0.08, "decoding frames for cut / motion analysis")
    F10 = frames(a.video, 10, sw, sh)
    gray = np.array([cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) for f in F10]).astype(np.float32)
    diff = np.array([0.0] + [np.abs(gray[i] - gray[i - 1]).mean() / 255 for i in range(1, len(gray))])
    thr = max(0.11, float(np.mean(diff) + 2.6 * np.std(diff)))
    cuts = []
    for i in range(1, len(diff) - 1):
        if diff[i] > thr and diff[i] >= diff[i - 1] and diff[i] >= diff[i + 1] and (not cuts or i - cuts[-1] >= 3):
            cuts.append(i)
    trans = []
    for i in cuts:
        hi = int((diff[max(0, i - 2):i + 3] > thr * 0.6).sum())
        dx, dy = 0.0, 0.0
        if i >= 1:
            (dx, dy), resp = cv2.phaseCorrelate(gray[i - 1], gray[i])
        kind = "cut"
        if hi >= 3 and abs(dy) > abs(dx) * 1.3 and abs(dy) > sh * 0.08: kind = "whip_v"
        elif hi >= 3 and abs(dx) > abs(dy) * 1.3 and abs(dx) > sw * 0.08: kind = "whip_h"
        elif hi >= 3: kind = "dissolve_or_motion"
        trans.append({"t": round(i / 10, 2), "kind": kind, "dir": ("up" if dy < 0 else "down") if kind == "whip_v" else None})
    cut_t = [c["t"] for c in trans]
    bounds = [0.0] + cut_t + [spec["duration"]]
    shots = [round(b - a_, 2) for a_, b in zip(bounds[:-1], bounds[1:]) if b - a_ > 0.05]

    progress(0.35, "classifying text cards vs footage, extracting colours")
    F2 = frames(a.video, 2, 320, max(2, int(round(320 / ar / 2)) * 2))
    card_cols, card_w, card_idx, word_cols, accent_px, ink_px, foot_idx = [], [], [], [], [], [], []
    bright = []
    for i, f in enumerate(F2):
        col, ratio = dominant(f)
        lum = col.mean(); bright.append(float(f.mean()))
        if ratio > 0.42 and lum > 150 or ratio > 0.62:
            card_idx.append(i); card_cols.append(col); card_w.append(ratio)
            px = f.reshape(-1, 3).astype(np.int16)
            d = np.abs(px - col).max(1)
            tint = px[(d > 8) & (d < 40)]
            if len(tint) > 50: word_cols.append(np.median(tint, 0))
            far = px[d > 80]
            if len(far):
                hsv = cv2.cvtColor(far.astype(np.uint8).reshape(-1, 1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3)
                accent_px.append(far[(hsv[:, 1] > 90) & (hsv[:, 2] > 60)][::7])
                ink_px.append(far[hsv[:, 2] < 70][::7])
        else:
            foot_idx.append(i)
    pal_cards = []
    for c, w in cluster_colors(card_cols, card_w, 26, 6):
        if c.mean() < 140: continue
        near = [wc for wc in word_cols if np.abs(wc - c).max() < 45]
        word = np.median(near, 0) if near else c * 0.94
        pal_cards.append({"bg": hexc(c), "word": hexc(word), "weight": round(float(w), 2)})
    accents = []
    acc = np.concatenate([x for x in accent_px if len(x)]) if any(len(x) for x in accent_px) else np.zeros((0, 3))
    if len(acc) > 30:
        Z = acc.astype(np.float32); K = min(5, len(Z))
        _, lab, cen = cv2.kmeans(Z, K, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0), 3, cv2.KMEANS_PP_CENTERS)
        cnt = np.bincount(lab.ravel(), minlength=K)
        accents = [hexc(cen[j]) for j in np.argsort(-cnt) if cnt[j] > len(Z) * 0.06]
    ink = hexc(np.median(np.concatenate(ink_px), 0)) if any(len(x) for x in ink_px) else None
    dark_cards = [hexc(c) for c, w in cluster_colors(card_cols, card_w, 26, 6) if c.mean() < 90]

    progress(0.55, "measuring camera motion / zoom in footage")
    moving, zooms = 0, []
    orb = cv2.ORB_create(400)
    G2 = [cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) for f in F2]
    pairs = [(i, i + 1) for i in foot_idx if i + 1 in foot_idx and not any(abs(t - (i + 0.5) / 2) < 0.5 for t in cut_t)]
    for i, j in pairs[:120]:
        k1, d1 = orb.detectAndCompute(G2[i], None); k2, d2 = orb.detectAndCompute(G2[j], None)
        if d1 is None or d2 is None or len(k1) < 12 or len(k2) < 12: continue
        m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d1, d2)
        if len(m) < 10: continue
        p1 = np.float32([k1[x.queryIdx].pt for x in m]); p2 = np.float32([k2[x.trainIdx].pt for x in m])
        M, inl = cv2.estimateAffinePartial2D(p1, p2, ransacReprojThreshold=2.0)
        if M is None: continue
        s = float(np.hypot(M[0, 0], M[1, 0])); tr = float(np.hypot(M[0, 2], M[1, 2]))
        zooms.append(s)
        if abs(s - 1) > 0.006 or tr > 1.5: moving += 1
    cam_moving = moving / max(1, len(zooms)) if zooms else 0.0
    zoom_rate = float(np.mean([abs(z - 1) for z in zooms]) * 2) if zooms else 0.0   # per second (2 fps pairs)

    progress(0.62, "looking for burned-in subtitles")
    sub_rows = []
    for i in foot_idx:
        f = F2[i]; h = f.shape[0]
        hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
        white = (hsv[..., 2] > 215) & (hsv[..., 1] < 45)
        edges = cv2.Canny(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), 80, 200) > 0
        band = (white & np.roll(edges, 1, 0)).mean(1)
        lo = int(h * 0.55)
        r = int(np.argmax(band[lo:])) + lo
        if band[r] > 0.025: sub_rows.append(r / h)
    subs = {"detected": len(sub_rows) > 0.3 * max(1, len(foot_idx)), "y": round(float(np.median(sub_rows)), 3) if sub_rows else None,
            "frames_with_text": len(sub_rows), "footage_frames": len(foot_idx)}
    footage_theme = "dark" if foot_idx and np.mean([bright[i] for i in foot_idx]) < 85 else "light" if foot_idx else None

    audio = {}
    if spec["has_audio"]:
        progress(0.68, "analysing music + narration")
        wav = os.path.join(a.out, "_audio.wav")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", a.video, "-vn", "-ac", "1", "-ar", "22050", wav], check=True)
        try:
            import librosa
            y, sr = librosa.load(wav, sr=22050)
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            rms = librosa.feature.rms(y=y, hop_length=2205)[0]
            cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
            onset = librosa.onset.onset_detect(y=y, sr=sr, units="time")
            audio = {"tempo_bpm": round(float(np.atleast_1d(tempo)[0]), 1), "rms_db": round(float(20 * np.log10(np.mean(rms) + 1e-9)), 1),
                     "brightness_hz": round(float(np.median(cent))), "onsets_per_s": round(len(onset) / max(1, len(y) / sr), 2),
                     "energy": "high" if np.mean(rms) > 0.12 else "medium" if np.mean(rms) > 0.05 else "low",
                     "envelope": [round(float(x), 4) for x in (rms / (rms.max() + 1e-9))[:600]]}
        except Exception as e:
            audio = {"error": str(e)}
        if a.whisper != "none":  # narration pace / language
            progress(0.78, "transcribing narration for pace / language")
            try:
                from framefoundry.audio.align import transcribe as _tr
                heard, inf = _tr(wav, None, cfg, vad=True)
                speech = (heard[-1][2] - heard[0][1]) if heard else 0.0
                audio["narration"] = {"language": inf["language"], "words": len(heard),
                                      "words_per_second": round(len(heard) / speech, 2) if speech > 1 else None,
                                      "speech_ratio": round(speech / max(1, spec["duration"]), 2),
                                      "transcript": inf["text"][:2000],
                                      "first_words_at": round(heard[0][1], 2) if heard else None}
            except Exception as e:  # noqa: BLE001
                audio["narration"] = {"error": str(e)}
        os.remove(wav)

    progress(0.92, "contact sheet")
    n = len(F2); idx = np.linspace(0, n - 1, 12).astype(int) if n else []
    if len(idx):
        th = [cv2.resize(F2[i], (320, int(320 / ar))) for i in idx]
        rows = [np.concatenate(th[r * 4:(r + 1) * 4], 1) for r in range(3)]
        cv2.imwrite(os.path.join(a.out, "contact.jpg"), cv2.cvtColor(np.concatenate(rows, 0), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 82])
    # opening: is the hook typographic?
    first_footage = next((i / 2 for i in foot_idx), None)
    res = {"spec": spec, "cuts": cut_t, "transitions": trans, "shots": shots,
           "shot_stats": {"count": len(shots), "median": round(float(np.median(shots)), 2) if shots else None,
                          "mean": round(float(np.mean(shots)), 2) if shots else None,
                          "cuts_per_min": round(len(cut_t) / max(1e-3, spec["duration"]) * 60, 1)},
           "transition_mix": {k: sum(1 for t in trans if t["kind"] == k) for k in ("cut", "whip_v", "whip_h", "dissolve_or_motion")},
           "cards": {"ratio": round(len(card_idx) / max(1, n), 2), "palette": pal_cards, "dark_cards": dark_cards,
                     "accents": accents, "ink": ink, "opening_is_typographic": bool(card_idx and card_idx[0] == 0),
                     "first_footage_at": first_footage, "ends_on_dark_card": bool(n and (n - 1) in card_idx and dark_cards)},
           "camera": {"moving_ratio": round(cam_moving, 2), "zoom_rate_per_s": round(zoom_rate, 4), "pairs": len(zooms)},
           "subtitles": subs, "footage_theme": footage_theme, "audio": audio}
    json.dump(res, open(os.path.join(a.out, "analysis.json"), "w"), indent=1, ensure_ascii=False)
    progress(1.0, f"{len(cut_t)} cuts, {len(pal_cards)} card colours, subtitles {'yes' if subs['detected'] else 'no'}")
    return res
