"""Word timings for narration: faster-whisper word timestamps + cross-script fuzzy alignment.

The script text is known, so recognition only has to *locate* words, not get them right:
  1. faster-whisper (MIT weights) transcribes with word timestamps (language hint: en / hi)
  2. script words and heard words are compared by a phonetic key that ignores script
     (Devanagari is romanised first: "संगीत" ~ "sangeet"), so Whisper may answer in Devanagari,
     Latin or a mix — common for Hinglish — and still align
  3. a dynamic-programming alignment keeps the order, allows one script word to cover two heard
     words ("SunoSunao" -> "Suno" "Sunao"), and interpolates unmatched words by length
Without a model (backend "estimate") words are spread over the detected speech regions.
Replaces the CC-BY-NC MMS aligner used by the original project.
"""
import functools
import re

import numpy as np

from framefoundry.language.hinglish import match_key
from framefoundry.util import FrameFoundryError, cpu_count, info, warn

WORD_RE = re.compile(r"[\wऀ-ॿ'’-]+", re.UNICODE)


def spoken_words(text):
    """Words of a narration string (punctuation stripped, emphasis marks removed)."""
    return [w for w in text.replace("—", " ").replace("*", "").split() if WORD_RE.search(w)]


@functools.lru_cache(maxsize=2)
def _whisper(model, device, compute_type):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise FrameFoundryError("faster-whisper is not installed (run ./install.sh)") from None
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"
    if device == "cpu" and compute_type == "float16":
        compute_type = "int8"
    kw = dict(device=device, compute_type=compute_type, cpu_threads=max(1, cpu_count() - 2))
    try:                       # offline first: no network request when the model is already in models/
        m = WhisperModel(model, local_files_only=True, **kw)
        info(f"Whisper '{model}' loaded from models/ ({device}, {compute_type})")
        return m
    except Exception:  # noqa: BLE001 - not cached yet
        info(f"downloading Whisper '{model}' into models/ (first use only)")
        return WhisperModel(model, **kw)


def transcribe(audio, language=None, cfg=None, vad=False, initial_prompt=None):
    """-> (heard [(word, start, end, prob)], info dict)"""
    a = (cfg or {}).get("alignment", {}) if isinstance(cfg, dict) else cfg.main["alignment"]
    m = _whisper(a.get("whisper_model", "large-v3-turbo"), a.get("device", "cpu"), a.get("compute_type", "int8"))
    pcm = load_16k(audio)            # decode with FFmpeg ourselves (independent of PyAV's API changes)
    segs, inf = m.transcribe(pcm, language=language, word_timestamps=True, beam_size=a.get("beam_size", 5),
                             vad_filter=vad, condition_on_previous_text=False, initial_prompt=initial_prompt)
    heard = []
    text = []
    for s in segs:
        text.append(s.text.strip())
        for w in (s.words or []):
            t = w.word.strip()
            if t:
                heard.append((t, float(w.start), float(w.end), float(getattr(w, "probability", 1.0))))
    return heard, {"language": inf.language, "language_probability": round(float(inf.language_probability), 3),
                   "text": " ".join(text).strip()}


def load_16k(path):
    """Any audio/video file -> mono float32 numpy array at 16 kHz (Whisper's input format)."""
    import subprocess
    from framefoundry.util import ffmpeg_bin
    r = subprocess.run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
                        "-ar", "16000", "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        raise FrameFoundryError(f"cannot decode audio {path}: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout, np.float32).copy()


def _sim(a, b):
    """Similarity of two phonetic keys in [0, 1] (normalised Levenshtein)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != b[j - 1]))
        prev = cur
    return 1.0 - prev[lb] / max(la, lb)


def align(script_words, heard, span=None, min_sim=0.45):
    """Map script words onto heard (word, s, e, ...) tuples.
    Returns [{'w', 's', 'e', 'matched': bool}] in script order (always one entry per script word)."""
    n, m = len(script_words), len(heard)
    if n == 0:
        return []
    if m == 0:
        a, b = span if span else (0.0, 1.0)
        return estimate(script_words, a, b)
    sk = [match_key(w) for w in script_words]
    hk = [match_key(h[0]) for h in heard]
    NEG = -1e9
    score = np.full((n + 1, m + 1), NEG)
    back = np.zeros((n + 1, m + 1, 2), dtype=np.int32)
    score[0, :] = 0.0
    score[:, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, arg = score[i - 1, j], (i - 1, j)            # skip script word
            if score[i, j - 1] > best:
                best, arg = score[i, j - 1], (i, j - 1)        # skip heard word
            s = _sim(sk[i - 1], hk[j - 1])
            if s >= min_sim and score[i - 1, j - 1] + s > best:
                best, arg = score[i - 1, j - 1] + s, (i - 1, j - 1)
            if j >= 2:                                         # one script word = two heard words
                s2 = _sim(sk[i - 1], hk[j - 2] + hk[j - 1])
                if s2 >= max(min_sim, 0.6) and score[i - 1, j - 2] + s2 + 0.05 > best:
                    best, arg = score[i - 1, j - 2] + s2 + 0.05, (i - 1, j - 2)
            score[i, j] = best
            back[i, j] = arg
    # trace back
    times = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        pi, pj = back[i, j]
        if pi == i - 1 and pj <= j - 1:      # matched script word i-1 to heard pj..j-1
            times[i - 1] = (heard[pj][1], heard[j - 1][2])
        i, j = int(pi), int(pj)
    s_all = span[0] if span else heard[0][1]
    e_all = span[1] if span else heard[-1][2]
    anchors = [k for k, t in enumerate(times) if t]
    out = []
    for k, w in enumerate(script_words):
        if times[k]:
            out.append({"w": w, "s": round(times[k][0], 3), "e": round(max(times[k][1], times[k][0] + 0.04), 3), "matched": True})
            continue
        prev = max((x for x in anchors if x < k), default=None)
        nxt = min((x for x in anchors if x > k), default=None)
        a = times[prev][1] if prev is not None else s_all
        b = times[nxt][0] if nxt is not None else e_all
        lo = prev + 1 if prev is not None else 0
        hi = nxt if nxt is not None else n
        group = script_words[lo:hi]
        L = np.array([max(1, len(x)) + 1.5 for x in group], float)
        c = np.concatenate([[0], np.cumsum(L)]) / L.sum()
        idx = k - lo
        out.append({"w": w, "s": round(a + (b - a) * c[idx], 3), "e": round(a + (b - a) * c[idx + 1], 3), "matched": False})
    # monotonic clean-up
    for k in range(1, len(out)):
        if out[k]["s"] < out[k - 1]["s"]:
            out[k]["s"] = out[k - 1]["s"]
        if out[k]["e"] < out[k]["s"]:
            out[k]["e"] = out[k]["s"] + 0.04
    return out


def snap_to_onsets(words, regions, max_shift=0.35):
    """Whisper tends to place word starts slightly early (in the silence before the word). A start that lies
    in silence is moved to the next speech onset when that onset is within max_shift seconds."""
    if not regions:
        return words
    starts = [r[0] for r in regions]
    import bisect
    for w in words:
        s = w["s"]
        k = bisect.bisect_right(starts, s) - 1
        inside = k >= 0 and regions[k][0] <= s < regions[k][1]
        if not inside and k + 1 < len(regions):
            nxt = regions[k + 1][0]
            if 0 < nxt - s <= max_shift and nxt < w["e"]:
                w["s"] = round(nxt, 3)
    return words


def estimate(script_words, a, b, regions=None):
    """No recogniser: spread words over [a, b] (or over speech regions) proportionally to length."""
    if not script_words:
        return []
    L = np.array([max(2, len(w)) + 1.5 for w in script_words], float)
    c = np.concatenate([[0], np.cumsum(L)]) / L.sum()
    if regions:
        total = sum(e - s for s, e in regions)
        cum = np.concatenate([[0], np.cumsum([e - s for s, e in regions])]) / max(total, 1e-6)

        def at(x, end=False):
            # a word that ends exactly on a region boundary ends in that region, not at the next one's start
            k = int(np.searchsorted(cum, x, side="left" if end else "right") - 1)
            k = min(max(k, 0), len(regions) - 1)
            s, e = regions[k]
            return s + (x - cum[k]) / max(cum[k + 1] - cum[k], 1e-9) * (e - s)
        return [{"w": w, "s": round(at(c[i]), 3), "e": round(at(c[i + 1], end=True), 3), "matched": False}
                for i, w in enumerate(script_words)]
    span = max(0.1, b - a)
    return [{"w": w, "s": round(a + c[i] * span, 3), "e": round(a + c[i + 1] * span, 3), "matched": False}
            for i, w in enumerate(script_words)]


def align_take(audio, text, language, cfg, offset=0.0, backend=None):
    """Word timings for one narration take (seconds relative to the trimmed take)."""
    import soundfile as sf
    from framefoundry.audio.process import speech_regions
    words = spoken_words(text)
    y, sr = sf.read(str(audio), dtype="float32", always_2d=True)
    y = y.mean(1)
    regs = speech_regions(y, sr)
    span = (regs[0][0], regs[-1][1]) if regs else (0.0, len(y) / sr)
    backend = backend or cfg.main["alignment"]["backend"]
    method = "estimated"
    res = None
    if backend == "whisper":
        try:
            heard, _ = transcribe(audio, language, cfg)
            res = snap_to_onsets(align(words, heard, span), speech_regions(y, sr, top_db=35, min_gap=0.06))
            matched = sum(r["matched"] for r in res) / max(1, len(res))
            method = "aligned" if matched >= 0.5 else "partly-aligned"
        except FrameFoundryError:
            raise
        except Exception as e:  # noqa: BLE001 - fall back to estimation, but say so
            warn(f"word alignment failed ({e}); estimating word timings")
    if res is None:
        res = estimate(words, *span, regions=regs)
    for r in res:
        r["s"] = round(r["s"] - offset, 3)
        r["e"] = round(r["e"] - offset, 3)
    return res, method


def align_narration(audio, lines, language, cfg, backend=None):
    """A complete (uploaded) narration + its script lines -> per-line timing.
    lines: [{'id', 'spoken'}] -> {line_id: {start, end, words, words_method}}, report"""
    import soundfile as sf
    from framefoundry.audio.process import speech_regions
    y, sr = sf.read(str(audio), dtype="float32", always_2d=True)
    y = y.mean(1)
    dur = len(y) / sr
    regs = speech_regions(y, sr, top_db=40)
    if not regs:
        raise FrameFoundryError(f"no speech found in the narration {audio}")
    all_words, owner = [], []
    for L in lines:
        ws = spoken_words(L["spoken"])
        all_words += ws
        owner += [L["id"]] * len(ws)
    backend = backend or cfg.main["alignment"]["backend"]
    res, report = None, {"backend": backend}
    if backend == "whisper":
        try:
            heard, inf = transcribe(audio, language, cfg, vad=False)
            report.update({"heard_words": len(heard), "detected_language": inf["language"], "transcript": inf["text"][:4000]})
            res = snap_to_onsets(align(all_words, heard, (regs[0][0], regs[-1][1])), speech_regions(y, sr, top_db=35, min_gap=0.06))
        except FrameFoundryError:
            raise
        except Exception as e:  # noqa: BLE001
            warn(f"transcription failed ({e}); estimating timing from pauses")
            report["error"] = str(e)
    if res is None:
        res = _estimate_by_pauses(lines, regs)
        report["backend"] = "estimate"
    timing = {}
    for L in lines:
        ws = [r for r, o in zip(res, owner) if o == L["id"]]
        if not ws:
            continue
        matched = sum(w["matched"] for w in ws) / len(ws)
        timing[L["id"]] = {"start": ws[0]["s"], "end": ws[-1]["e"], "words": [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in ws],
                           "words_method": "aligned" if matched >= 0.5 else ("partly-aligned" if matched > 0 else "estimated"),
                           "match_ratio": round(matched, 2)}
    report["matched_ratio"] = round(sum(r["matched"] for r in res) / max(1, len(res)), 3)
    report["duration"] = round(dur, 3)
    return timing, report


def _estimate_by_pauses(lines, regs):
    """Assign lines to the speech between the largest pauses, words proportionally inside."""
    words = [spoken_words(L["spoken"]) for L in lines]
    n = len(lines)
    gaps = sorted(range(len(regs) - 1), key=lambda k: regs[k + 1][0] - regs[k][1], reverse=True)[: max(0, n - 1)]
    cuts = sorted(gaps)
    if len(cuts) == n - 1:
        groups, start = [], 0
        for c in cuts + [len(regs) - 1]:
            groups.append(regs[start:c + 1])
            start = c + 1
    else:
        a, b = regs[0][0], regs[-1][1]
        tot = sum(len(w) for w in words) or 1
        groups, t = [], a
        for w in words:
            d = (b - a) * len(w) / tot
            groups.append([(t, t + d)])
            t += d
    out = []
    for ws, g in zip(words, groups):
        out += estimate(ws, g[0][0], g[-1][1], regions=g)
    return out
