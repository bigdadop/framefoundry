"""Prepare an authorised voice sample as a cloning reference (all local).

1. decode any audio/video file; 2. optional Demucs vocal isolation when music is detected;
3. pick the cleanest continuous 5-11 s speech window; 4. measure pitch / energy / pace;
5. (for OmniVoice) transcribe the window — the model needs the reference text.
Cloning is only done when the user confirms they have the speaker's permission (--confirm-voice-permission).
"""
import os

import numpy as np
import soundfile as sf

from framefoundry.audio.process import decode
from framefoundry.util import FrameFoundryError, info, jdump, warn


def music_score(y, sr):
    """Rough 'is there music under this speech?' score: energy left in the pauses."""
    import librosa
    if len(y) < sr:
        return 0.0
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    quiet = rms < np.percentile(rms, 25)
    return float(np.clip(np.median(rms[quiet]) / (np.median(rms) + 1e-9) * 2.2, 0, 1))


def best_window(y, sr, lo=5.0, hi=11.0):
    import librosa
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    voiced = rms > max(np.percentile(rms, 30), rms.max() * 0.04)
    fps = sr / hop
    total = len(y) / sr
    best, best_score = (0.0, min(total, hi)), -1.0
    for start in np.arange(0, max(0.01, total - lo), 0.25):
        for L in (hi, (lo + hi) / 2, lo):
            if start + L > total:
                continue
            a, b = int(start * fps), int((start + L) * fps)
            seg = voiced[a:b]
            if not len(seg):
                continue
            edge = (1 - voiced[max(0, a - 2):a + 2].mean() if a > 2 else 1) + (1 - voiced[max(0, b - 2):b + 2].mean())
            score = seg.mean() + 0.15 * edge + 0.02 * L
            if score > best_score:
                best_score, best = score, (float(start), float(start + L))
            break
    return best


def prepare(src, out_dir, cfg, transcribe_text=False, language=None):
    rc = cfg.voice["reference"]
    os.makedirs(out_dir, exist_ok=True)
    full = os.path.join(out_dir, "reference_decoded.wav")
    decode(src, full, 24000, 1)
    import librosa
    y, sr = librosa.load(full, sr=24000, mono=True)
    if len(y) < sr * 3:
        raise FrameFoundryError("the voice reference must contain at least 3 seconds (ideally 10-30 s) of clear speech")
    ms = music_score(y, sr)
    sep = rc.get("separate_music", "auto")
    separated = False
    if sep == "yes" or (sep == "auto" and ms > 0.35):
        try:
            from framefoundry.audio.separation import separate
            v = separate(full, os.path.join(out_dir, "separation"), want=("vocals",))["vocals"]
            y, sr = librosa.load(v, sr=24000, mono=True)
            separated = True
        except FrameFoundryError as e:
            warn(f"background music detected (score {ms:.2f}) but separation is unavailable: {e}")
    s0, s1 = best_window(y, sr, rc["min_seconds"], rc["max_seconds"])
    clip = y[int(s0 * sr):int(s1 * sr)]
    clip = clip / (np.abs(clip).max() + 1e-9) * 0.89
    f = int(0.01 * sr)
    clip[:f] *= np.linspace(0, 1, f)
    clip[-f:] *= np.linspace(1, 0, f)
    ref = os.path.join(out_dir, "voice_reference.wav")
    sf.write(ref, clip, sr, subtype="PCM_16")
    f0, vf, _ = librosa.pyin(clip, fmin=65, fmax=420, sr=sr, frame_length=2048)
    f0 = f0[vf & ~np.isnan(f0)] if f0 is not None else np.array([])
    pitch = [float(np.median(f0)), float(np.percentile(f0, 10)), float(np.percentile(f0, 90))] if len(f0) else [0, 0, 0]
    text = None
    if transcribe_text:
        from framefoundry.audio.align import transcribe
        heard, inf = transcribe(ref, language, cfg)
        text = inf["text"]
        open(os.path.join(out_dir, "voice_reference.txt"), "w", encoding="utf-8").write(text)
    res = {"source": os.path.basename(str(src)), "window_s": [round(s0, 2), round(s1, 2)], "duration": round((s1 - s0), 2),
           "music_score": round(ms, 2), "vocals_separated": separated, "pitch_hz_median": round(pitch[0]),
           "pitch_range_hz": round(pitch[2] - pitch[1]), "transcript": text}
    jdump(res, os.path.join(out_dir, "voice_reference.json"))
    info(f"voice reference: {res['duration']}s window from {res['source']} (pitch ~{res['pitch_hz_median']} Hz"
         f"{', vocals separated from music' if separated else ''})")
    return ref, text, res
