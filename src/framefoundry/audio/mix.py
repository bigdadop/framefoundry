"""Arrange background music to the video length and mix it under the narration.

Arrangement: start at `offset` in the track, place it at `at` seconds, loop on bar boundaries (beat
tracked, equal-power crossfade) when the track is too short, fade in / out, end exactly at the video end.
Mix: music levelled to (narration LUFS + bed_db), sidechain ducking keeps the voice on top, final master
to the configured loudness (default -14 LUFS / -1 dBTP), 48 kHz stereo.
(Level / ducking chain from the SunoSunao mix_audio.py; also fixes its 192 kHz music-bed export.)
"""
import os

import numpy as np
import soundfile as sf

from framefoundry.audio.process import SR, loudnorm_2pass
from framefoundry.util import FrameFoundryError, ffmpeg, info


def load_stereo(path):
    tmp = str(path) + ".dec.wav"
    ffmpeg("-i", path, "-vn", "-ac", "2", "-ar", str(SR), "-c:a", "pcm_f32le", tmp)
    y, _ = sf.read(tmp, dtype="float32", always_2d=True)
    os.remove(tmp)
    if not len(y):
        raise FrameFoundryError(f"music file {path} contains no audio")
    return y


def bar_loop_points(y):
    try:
        import librosa
        mono = y.mean(1)[: SR * 120]
        tempo, beats = librosa.beat.beat_track(y=librosa.resample(mono, orig_sr=SR, target_sr=22050), sr=22050, units="time")
        tempo = float(np.atleast_1d(tempo)[0])
        if tempo < 50 or len(beats) < 9:
            return None, tempo
        bar = 4 * 60.0 / tempo
        first = float(beats[0])
        n_bars = int((len(y) / SR - first) // bar)
        if n_bars < 2:
            return None, tempo
        return (first, first + n_bars * bar), tempo
    except Exception:  # noqa: BLE001 - beat tracking is best effort
        return None, 0.0


def arrange(y, total, offset=0.0, at=0.0, loop=True, fade_in=0.03, fade_out=1.5):
    y = y[int(offset * SR):]
    n_need = int(max(0.0, total - at) * SR)
    tempo = 0.0
    if len(y) < n_need and loop:
        pts, tempo = bar_loop_points(y)
        a, b = (int(pts[0] * SR), int(pts[1] * SR)) if pts else (0, len(y))
        body = y[a:b]
        xf = min(int(0.05 * SR), len(body) // 4)
        out = y[:b]
        t = np.linspace(0, np.pi / 2, xf)[:, None]
        while len(out) < n_need + xf:
            out = np.concatenate([out[:-xf], out[-xf:] * np.cos(t) + body[:xf] * np.sin(t), body[xf:]])
        y = out
    y = y[:n_need].copy()
    if len(y) < n_need:
        y = np.concatenate([y, np.zeros((n_need - len(y), 2), np.float32)])
    fi = min(len(y), int(max(0.01, fade_in) * SR))
    y[:fi] *= np.linspace(0, 1, fi)[:, None]
    fo = min(len(y), int(fade_out * SR))
    if fo > 0:
        y[-fo:] *= (np.linspace(1, 0, fo)[:, None] ** 1.5)
    full = np.concatenate([np.zeros((int(at * SR), 2), np.float32), y])
    return full[: int(total * SR)], tempo


def mix(voice, total, out_dir, cfg, music=None, profile_music=None, offset=0.0, at=0.0, volume=1.0):
    """voice: narration wav or None; returns (final_audio_path, info)."""
    a = cfg.main["audio"]
    pm = profile_music or {}
    bed_db = pm.get("bed_db", a["music_bed_db"])
    duck = a.get("ducking", True) and pm.get("duck", True)
    target = pm.get("target_lufs", a["master_lufs"])
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, "final_audio.wav")
    res = {"total": round(total, 3), "music": None, "ducking": False, "target_lufs": target}
    if music:
        info("arranging background music")
        y = load_stereo(music)
        bed, tempo = arrange(y, total, offset, at, a.get("loop_music", True), pm.get("fade_in", a["music_fade_in"]),
                             pm.get("fade_out", a["music_fade_out"]))
        res["music_tempo_bpm"] = round(tempo, 1)
        raw = os.path.join(out_dir, "music_arranged_raw.wav")
        sf.write(raw, bed, SR, subtype="FLOAT")
        bed_path = os.path.join(out_dir, "background_music.wav")
        level = a["narration_lufs"] + bed_db + 20 * np.log10(max(1e-3, volume))
        loudnorm_2pass(raw, bed_path, target=level, tp=-2.0, lra=11, channels=2)
        os.remove(raw)
        res["music"] = os.path.basename(str(music))
        res["music_level_lufs"] = round(level, 1)
    if voice and music:
        duck_f = "[m][vsc]sidechaincompress=threshold=0.05:ratio=2.2:attack=15:release=250:makeup=1[md];" if duck else ""
        fc = (f"[0:a]aresample={SR},pan=stereo|c0=c0|c1=c0,apad=whole_dur={total:.3f},asplit=2[v][vsc];"
              f"[1:a]aresample={SR}[m];" + duck_f + ("" if duck else "[vsc]anullsink;") +
              f"[v]{'[md]' if duck else '[m]'}amix=inputs=2:normalize=0:duration=longest,atrim=0:{total:.3f},"
              f"afade=t=in:st=0:d=0.02[out]")
        premix = os.path.join(out_dir, "premix.wav")
        ffmpeg("-i", voice, "-i", bed_path, "-filter_complex", fc, "-map", "[out]", "-c:a", "pcm_f32le", premix)
        res["ducking"] = duck
    elif voice:
        premix = os.path.join(out_dir, "premix.wav")
        ffmpeg("-i", voice, "-af", f"aresample={SR},pan=stereo|c0=c0|c1=c0,apad=whole_dur={total:.3f},atrim=0:{total:.3f}",
               "-c:a", "pcm_f32le", premix)
    elif music:
        premix = bed_path
    else:
        raise FrameFoundryError("nothing to mix: no narration and no music")
    # master: loudness + a true-peak-safe limiter (FFmpeg alimiter, 4 ms look-ahead) with headroom for AAC
    loudnorm_2pass(premix, final, target=target, tp=a["true_peak"] - 0.5, lra=9, channels=2,
                   post_filter="alimiter=limit=0.84:attack=4:release=60:level=false")
    if premix != (bed_path if music else None) and os.path.exists(premix):
        os.remove(premix)
    return final, res
