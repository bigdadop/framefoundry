"""Narration: assemble generated per-line takes, or import an uploaded voice-over.

Both paths end with the same artefacts in the campaign folder:
  voiceover.wav          processed narration, 48 kHz mono, normalised to the configured LUFS
  project_files/timing.json   {line_id: {start, end, words: [{w, s, e}], words_method}}
"""
import os
import tempfile

import numpy as np
import soundfile as sf

from framefoundry.audio import align as A
from framefoundry.audio.process import (DENOISE, LIGHT_CHAIN, SR, VOICE_CHAIN, decode, fade_edges, loudnorm_2pass,
                                        read_mono, speech_regions, trim)
from framefoundry.util import FrameFoundryError, ffmpeg, info, warn


def assemble(lines, takes, cfg, out_dir, language, words_by_line=None):
    """lines: [{id, spoken, section, gap_after?}], takes: {line_id: wav path} -> (voice_wav, timing)."""
    asm = cfg.voice["assembly"]
    a_cfg = cfg.main["audio"]
    parts = [np.zeros(int(asm["lead_in"] * SR), np.float32)]
    t = asm["lead_in"]
    timing = {}
    for i, L in enumerate(lines):
        y = read_mono(takes[L["id"]])
        y, off = trim(y, SR) if a_cfg.get("trim_silence", True) else (y, 0.0)
        y = fade_edges(y.copy(), SR)
        dur = len(y) / SR
        wl = (words_by_line or {}).get(L["id"])
        method = wl[1] if wl else "estimated"
        ws = wl[0] if wl else A.estimate(A.spoken_words(L["spoken"]), 0.03, dur - 0.03)
        ws = [{"w": w["w"], "s": round(t + max(0.0, w["s"] - (off if wl else 0)), 3),
               "e": round(t + min(dur, w["e"] - (off if wl else 0)), 3)} for w in ws]
        timing[L["id"]] = {"start": round(t, 3), "end": round(t + dur, 3), "words": ws, "words_method": method}
        parts.append(y)
        t += dur
        if i + 1 < len(lines):
            gap = L.get("gap_after")
            if gap is None:
                gap = asm["line_gap"] + (asm["section_gap"] - asm["line_gap"] if lines[i + 1].get("section") != L.get("section") else 0)
            parts.append(np.zeros(int(gap * SR), np.float32))
            t += gap
    parts.append(np.zeros(int(asm.get("tail", 0.25) * SR), np.float32))
    v = np.concatenate(parts)
    os.makedirs(out_dir, exist_ok=True)
    raw = os.path.join(out_dir, "voiceover_raw.wav")
    sf.write(raw, v, SR, subtype="PCM_24")
    out = os.path.join(out_dir, "voiceover.wav")
    loudnorm_2pass(raw, out, target=a_cfg["narration_lufs"], tp=-1.5, lra=7, pre_filter=VOICE_CHAIN, channels=1)
    return out, timing


def import_voiceover(src, lines, cfg, out_dir, language_hint, processing="light"):
    """Uploaded narration -> processed voiceover.wav + per-line/word timing (via alignment)."""
    if not os.path.isfile(src):
        raise FrameFoundryError(f"voice-over not found: {src}")
    a_cfg = cfg.main["audio"]
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        dec = os.path.join(tmp, "decoded.wav")
        chain = []
        if a_cfg.get("noise_reduction"):
            chain.append(DENOISE)
        chain.append(VOICE_CHAIN if processing == "full" else LIGHT_CHAIN)
        decode(src, dec, SR, 1)
        y = read_mono(dec)
        if len(y) < SR * 0.5:
            raise FrameFoundryError(f"voice-over {src} is shorter than half a second")
        regs = speech_regions(y, SR, top_db=40)
        if not regs:
            raise FrameFoundryError(f"no speech detected in {src}")
        # keep the speaker's own pacing; only trim long leading/trailing silence
        a = max(0, int((regs[0][0] - 0.15) * SR))
        b = min(len(y), int((regs[-1][1] + 0.25) * SR))
        trimmed = os.path.join(tmp, "trimmed.wav")
        sf.write(trimmed, y[a:b], SR, subtype="FLOAT")
        out = os.path.join(out_dir, "voiceover.wav")
        loudnorm_2pass(trimmed, out, target=a_cfg["narration_lufs"], tp=-1.5, lra=9, pre_filter=",".join(chain), channels=1)
    info(f"voice-over imported ({(b - a) / SR:.1f}s of narration, leading silence trimmed {a / SR:.2f}s)")
    timing, report = A.align_narration(out, lines, language_hint, cfg)
    missing = [L["id"] for L in lines if L["id"] not in timing]
    if missing:
        warn(f"no timing for lines {missing} (they have no speakable words)")
    if report.get("matched_ratio", 0) < 0.4 and report.get("backend") == "whisper":
        warn(f"only {report['matched_ratio']:.0%} of script words were found in the voice-over — "
             "check that the narration matches the script (timing for unmatched words is interpolated)")
    return out, timing, report


def extract_audio(video, dst):
    """Narration from a video file (e.g. an existing recorded explainer)."""
    ffmpeg("-i", video, "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", dst)
    return dst
