"""Audio helpers built on FFmpeg + numpy: decoding, voice chain, loudness, silence detection."""
import json
import os
import re

import numpy as np
import soundfile as sf

from framefoundry.util import FrameFoundryError, ffmpeg, ffmpeg_bin, run

SR = 48000

# broadcast-style narration chain (from the SunoSunao assemble_voice.py)
VOICE_CHAIN = ("highpass=f=75,lowpass=f=15500,"
               "equalizer=f=250:t=q:w=1.0:g=-1.5,equalizer=f=3600:t=q:w=1.2:g=2.0,equalizer=f=10500:t=q:w=1.0:g=1.0,"
               "deesser=i=0.35:m=0.5:f=0.5,"
               "acompressor=threshold=-21dB:ratio=3:attack=4:release=70:makeup=2")
LIGHT_CHAIN = "highpass=f=60,lowpass=f=16500"
DENOISE = "afftdn=nr=12:nf=-42:tn=1"


def decode(src, dst, sr=SR, channels=1, extra_filter=None):
    af = ["-af", extra_filter] if extra_filter else []
    ffmpeg("-i", src, "-vn", "-ac", str(channels), "-ar", str(sr), *af, "-c:a", "pcm_f32le", dst)
    return dst


def read_mono(path, sr=SR):
    y, r = sf.read(path, dtype="float32", always_2d=True)
    y = y.mean(1)
    if r != sr:
        import librosa
        y = librosa.resample(y, orig_sr=r, target_sr=sr)
    return y


def loudness_stats(path):
    """Integrated loudness (LUFS), true peak (dBTP) and LRA via FFmpeg's EBU R128 meter."""
    r = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
            check=False, capture=True)
    log = r.stderr
    i = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", log)
    tp = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", log)
    lra = re.findall(r"LRA:\s+([\d.]+) LU", log)

    def f(x):
        return float(x) if x not in ("-inf", "inf") else float("-inf")
    return {"integrated_lufs": f(i[-1]) if i else None, "true_peak_dbtp": f(tp[-1]) if tp else None,
            "lra": float(lra[-1]) if lra else None}


def loudnorm_2pass(src, dst, target=-14.0, tp=-1.0, lra=11.0, pre_filter=None, channels=None, codec="pcm_s24le",
                   post_filter=None):
    """Two-pass EBU R128 normalisation (accurate on short clips), resampled back to 48 kHz."""
    pre = (pre_filter + ",") if pre_filter else ""
    post = ("," + post_filter) if post_filter else ""
    r = run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", src, "-af",
             f"{pre}loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"], check=False, capture=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    ch = ["-ac", str(channels)] if channels else []
    if not m:
        ffmpeg("-i", src, "-af", f"{pre}loudnorm=I={target}:TP={tp}:LRA={lra},aresample={SR}{post}", *ch, "-ar", str(SR), "-c:a", codec, dst)
        return dst
    meas = json.loads(m.group(0))
    if meas.get("input_i") in ("-inf", None) or float(meas["input_i"]) < -70:
        raise FrameFoundryError(f"audio is silent: {src}")
    flt = (f"{pre}loudnorm=I={target}:TP={tp}:LRA={lra}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
           f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:"
           f"linear=true,aresample={SR}{post}")
    ffmpeg("-i", src, "-af", flt, *ch, "-ar", str(SR), "-c:a", codec, dst)
    return dst


def encode_aac_safe(wav, out, bitrate="256k", max_tp=-1.0, rate=SR, target_lufs=None):
    """Encode the final mix to AAC and verify its TRUE peak after encoding (AAC overshoots on transients);
    if it is above max_tp, lower the gain by the excess and re-encode. When that costs more than 1 dB of
    loudness (very peaky material), re-master once with loudnorm's dynamic mode (true-peak limiter) and keep
    the version closest to the loudness target. Returns (path, measured stats, gain_db)."""
    def encode(src, gain):
        af = ["-af", f"volume={gain:.2f}dB"] if gain else []
        ffmpeg("-i", src, *af, "-c:a", "aac", "-b:a", bitrate, "-ar", str(rate), out)
        return loudness_stats(out)
    gain = 0.0
    for _ in range(4):
        st = encode(wav, gain)
        tp = st["true_peak_dbtp"]
        if tp is None or tp <= max_tp:
            break
        gain -= (tp - max_tp) + 0.2
    i = st.get("integrated_lufs")
    if target_lufs is not None and i is not None and i < target_lufs - 1.0:
        dyn = str(out) + ".dyn.wav"
        ffmpeg("-i", wav, "-af", f"loudnorm=I={target_lufs}:TP={max_tp - 1.0}:LRA=11,aresample={rate}", "-ar", str(rate),
               "-c:a", "pcm_s24le", dyn)
        g2 = 0.0
        for _ in range(4):
            st2 = encode(dyn, g2)
            if st2["true_peak_dbtp"] is None or st2["true_peak_dbtp"] <= max_tp:
                break
            g2 -= (st2["true_peak_dbtp"] - max_tp) + 0.2
        os.remove(dyn)
        if st2["true_peak_dbtp"] is not None and st2["true_peak_dbtp"] <= max_tp and \
                abs(st2["integrated_lufs"] - target_lufs) < abs(i - target_lufs):
            return out, dict(st2, remastered="loudnorm-dynamic"), g2
        encode(wav, gain)                       # keep the linear version
    return out, st, gain


def speech_regions(y, sr, top_db=38, min_gap=0.12):
    """Non-silent regions [(start_s, end_s)] (librosa split), with tiny gaps merged."""
    import librosa
    idx = librosa.effects.split(y, top_db=top_db, frame_length=1024, hop_length=128)
    regs = []
    for a, b in idx:
        s, e = a / sr, b / sr
        if regs and s - regs[-1][1] < min_gap:
            regs[-1] = (regs[-1][0], e)
        else:
            regs.append((s, e))
    return regs


def trim(y, sr, top_db=38, pad=0.03):
    """Trim leading/trailing silence -> (trimmed, offset_s)."""
    regs = speech_regions(y, sr, top_db)
    if not regs:
        return y, 0.0
    a = max(0, int((regs[0][0] - pad) * sr))
    b = min(len(y), int((regs[-1][1] + pad) * sr))
    return y[a:b], a / sr


def rms_db(y):
    return float(20 * np.log10(np.sqrt(np.mean(np.square(y))) + 1e-12)) if len(y) else -120.0


def fade_edges(y, sr, ms=8):
    n = min(len(y) // 2, int(sr * ms / 1000))
    if n > 1:
        y[:n] *= np.linspace(0, 1, n)
        y[-n:] *= np.linspace(1, 0, n)
    return y
