"""Local voice generation: engine selection, licence gating, text preparation, take cache, read-back QA.

Engines run in their own virtual environments (see install.sh) through small worker scripts, so their
pinned torch/numpy versions never conflict with the core environment.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time

from framefoundry.audio import align as A
from framefoundry.language import hinglish as H
from framefoundry.language import normalize as N
from framefoundry.util import ROOT, FrameFoundryError, cpu_count, info, sha_file, sha_text, warn

HERE = os.path.dirname(os.path.abspath(__file__))

ENGINES = {
    "chatterbox": {
        "python": ROOT / ".venv-tts" / "bin" / "python",
        "worker": os.path.join(HERE, "worker_chatterbox.py"),
        "title": "Chatterbox Multilingual V3 (Resemble AI)",
        "licence": "MIT (code and weights); outputs carry an imperceptible Perth watermark",
        "commercial": True,
        "languages": {"english", "hindi", "hinglish"},
        "install": "./install.sh --with-tts",
    },
    "omnivoice": {
        "python": ROOT / ".venv-omnivoice" / "bin" / "python",
        "worker": os.path.join(HERE, "worker_omnivoice.py"),
        "title": "OmniVoice (k2-fsa)",
        "licence": "code Apache-2.0; pretrained weights CC-BY-NC (non-commercial); audio tokenizer under the "
                   "Boson Higgs Audio 2 Community License",
        "commercial": False,
        "languages": {"english", "hindi", "hinglish"},
        "install": "./install.sh --with-omnivoice --accept-noncommercial-models",
    },
}


def engine_status(name):
    e = ENGINES[name]
    if not e["python"].exists():
        return False, f"not installed ({e['install']})"
    return True, "installed"


def select_engine(cfg, requested=None):
    """-> engine name, or raise with a clear explanation. 'none' -> None."""
    v = cfg.voice
    name = (requested or v.get("engine") or "auto").lower()
    if name == "none":
        return None
    if name == "auto":
        ok, why = engine_status("chatterbox")
        if ok:
            return "chatterbox"
        raise FrameFoundryError("No local voice engine is installed and no --voiceover was given.\n"
                                "  - supply your narration:  --voiceover narration.wav\n"
                                f"  - or install the MIT-licensed engine:  {ENGINES['chatterbox']['install']}")
    if name not in ENGINES:
        raise FrameFoundryError(f"unknown voice engine '{name}' (choose: auto, {', '.join(ENGINES)}, none)")
    if not ENGINES[name]["commercial"] and not v.get("allow_non_commercial_models"):
        raise FrameFoundryError(f"{ENGINES[name]['title']} is licensed for NON-COMMERCIAL use only "
                                f"({ENGINES[name]['licence']}).\nTo use it for non-commercial work set "
                                "allow_non_commercial_models: true in config/voice_config.yaml.")
    ok, why = engine_status(name)
    if not ok:
        raise FrameFoundryError(f"voice engine {name} is {why}")
    return name


def tts_text(line, language, cfg):
    """Text actually sent to the voice engine for one script line."""
    if line.get("tts"):
        return line["tts"]
    lang_num = "english" if language == "english" else "hindi"
    words = []
    for tok in N.merge_currency_tokens(line["plain"].split()):
        words += N.spoken_tokens(tok, lang_num)
    text = " ".join(words)
    if language in ("hindi", "hinglish") and cfg.language["hinglish"].get("tts_hindi_words", "devanagari") == "devanagari":
        text = H.hindi_words_to_devanagari(text)      # Romanised Hindi words are read far better in Devanagari
    return text


def _char_error(expected, heard_words):
    a = "".join(H.match_key(w) for w in A.spoken_words(expected))
    b = "".join(H.match_key(w[0]) for w in heard_words)
    if not a:
        return 0.0
    return round(1.0 - A._sim(a, b) if b else 1.0, 3)


def _run_worker(name, job, label):
    e = ENGINES[name]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
        jp = f.name
    env = dict(os.environ, PYTHONNOUSERSITE="1", TOKENIZERS_PARALLELISM="false")
    env.pop("PYTHONPATH", None)
    models = os.path.dirname(os.environ.get("HF_HOME", str(ROOT / "models" / "huggingface")))
    env["PKUSEG_HOME"] = os.path.join(models, "pkuseg")     # chatterbox's tokenizer pulls a segmenter model; keep it in models/
    try:
        p = subprocess.Popen([str(e["python"]), e["worker"], jp], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env)
        result = None
        for line in p.stdout:
            line = line.rstrip()
            if line.startswith("PROGRESS "):
                parts = line.split(" ", 3)
                info(f"  [{label}] {parts[3] if len(parts) > 3 else ''}")
            elif line.startswith("RESULT "):
                result = json.loads(line[7:])
        err = p.stderr.read()
        if p.wait() != 0 or result is None:
            tail = "\n".join(x for x in err.splitlines() if x.strip())[-2500:]
            raise FrameFoundryError(f"{e['title']} failed:\n{tail}")
        return result
    finally:
        os.unlink(jp)


def synthesize(lines, language, cfg, takes_dir, engine=None, reference=None, reference_text=None,
               cache_dir=None, instruct=None):
    """lines: [{id, plain, tts?}] -> {'takes': {id: wav}, 'words': {id: (words, method)}, 'qa': {...}, 'engine': ...}"""
    name = engine or select_engine(cfg)
    vc = cfg.voice
    ecfg = dict(vc.get(name, {}))
    lang_id = cfg.language["tts_language"][language]
    whisper_lang = cfg.language["whisper_language"][language]
    os.makedirs(takes_dir, exist_ok=True)
    cache_dir = cache_dir or str(ROOT / "tmp" / "tts_cache")
    os.makedirs(cache_dir, exist_ok=True)
    ref_sig = sha_file(reference)[:16] if reference else (instruct or "default-voice")
    texts = {L["id"]: tts_text(L, language, cfg) for L in lines}
    seeds = {L["id"]: int(ecfg.get("seed", 0)) for L in lines}
    settings = {k: ecfg.get(k) for k in ("model", "exaggeration", "cfg_weight", "temperature", "steps", "guidance_scale")}

    def take_path(lid, seed):
        key = sha_text(name, settings, lang_id, ref_sig, texts[lid], seed)
        return os.path.join(cache_dir, f"{name}_{key}.wav")
    qa = {}
    best = {}
    todo = [L["id"] for L in lines]
    retries = int(ecfg.get("max_retries", 2)) if vc["qa"].get("asr_readback", True) else 0
    attempt = 0
    t0 = time.time()
    while todo:
        job_lines = []
        for lid in todo:
            p = take_path(lid, seeds[lid])
            if not os.path.exists(p):
                job_lines.append({"id": lid, "text": texts[lid], "out": p, "seed": seeds[lid]})
        if job_lines:
            info(f"generating {len(job_lines)} narration line(s) with {ENGINES[name]['title']} "
                 f"(language {lang_id}{', authorised voice reference' if reference else ''})")
            job = {"model": ecfg.get("model", "v3"), "device": ecfg.get("device", "cpu"), "language_id": lang_id,
                   "language": None, "reference": reference, "reference_text": reference_text, "instruct": instruct,
                   "exaggeration": ecfg.get("exaggeration", 0.5), "cfg_weight": ecfg.get("cfg_weight", 0.5),
                   "temperature": ecfg.get("temperature", 0.8), "steps": ecfg.get("steps", 32),
                   "guidance_scale": ecfg.get("guidance_scale", 2.0), "threads": max(1, cpu_count() - 1), "lines": job_lines}
            _run_worker(name, job, name)
        else:
            info(f"reusing {len(todo)} cached narration take(s)")
        failed = []
        for lid in todo:
            p = take_path(lid, seeds[lid])
            if not os.path.exists(p):
                raise FrameFoundryError(f"voice engine produced no audio for line {lid}")
            rec = {"take": os.path.basename(p), "seed": seeds[lid], "attempt": attempt + 1}
            words = None
            if vc["qa"].get("asr_readback", True):
                heard, inf = A.transcribe(p, whisper_lang, cfg)
                rec["heard"] = inf["text"]
                rec["char_error"] = _char_error(texts[lid], heard)
                import soundfile as sf
                y, sr = sf.read(p, dtype="float32", always_2d=True)
                from framefoundry.audio.process import speech_regions
                regs = speech_regions(y.mean(1), sr)
                span = (regs[0][0], regs[-1][1]) if regs else (0.0, len(y) / sr)
                words = A.snap_to_onsets(A.align(A.spoken_words(texts[lid]), heard, span),
                                         speech_regions(y.mean(1), sr, top_db=35, min_gap=0.06))
                matched = sum(w["matched"] for w in words) / max(1, len(words))
                rec["match_ratio"] = round(matched, 2)
            prev = best.get(lid)
            score = rec.get("char_error", 0.0)
            if prev is None or score < prev[0]:
                best[lid] = (score, p, words, rec)
            if vc["qa"].get("asr_readback", True) and score > vc["qa"]["max_char_error"] and attempt < retries:
                failed.append(lid)
                seeds[lid] += 1000
        todo = failed
        if failed:
            warn(f"read-back check failed for {failed}; regenerating with new seeds (attempt {attempt + 2})")
        attempt += 1
    takes, words_by_line = {}, {}
    for lid, (score, p, words, rec) in best.items():
        dst = os.path.join(takes_dir, f"{lid}.wav")
        shutil.copy2(p, dst)
        takes[lid] = dst
        if words:
            # word times are relative to the untrimmed take; assemble() subtracts the trim offset
            words_by_line[lid] = ([{"w": w["w"], "s": w["s"], "e": w["e"]} for w in words],
                                  "aligned" if rec.get("match_ratio", 0) >= 0.5 else "partly-aligned")
        rec["passed"] = score <= vc["qa"]["max_char_error"] if "char_error" in rec else None
        rec["tts_text"] = texts[lid]
        qa[lid] = rec
    bad = [k for k, r in qa.items() if r.get("passed") is False]
    if bad:
        warn(f"narration lines {bad} still differ from the script after {retries + 1} attempt(s) — listen to them "
             f"(see voice_qa.json); consider a {{tts: …}} pronunciation override")
    return {"engine": name, "title": ENGINES[name]["title"], "licence": ENGINES[name]["licence"],
            "commercial": ENGINES[name]["commercial"], "takes": takes, "words": words_by_line, "qa": qa,
            "seconds": round(time.time() - t0, 1), "language_id": lang_id}
