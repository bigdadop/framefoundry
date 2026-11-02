#!/usr/bin/env python3
"""FrameFoundry voice worker — Chatterbox Multilingual (Resemble AI, MIT licence).

Runs inside .venv-tts (its own torch/numpy pins), so it imports nothing from FrameFoundry.
Usage: python worker_chatterbox.py JOB.json
JOB = {"model": "v3", "device": "cpu", "language_id": "en"|"hi", "reference": wav|null,
       "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "threads": N,
       "lines": [{"id": "L01", "text": "...", "out": "/abs/L01.wav", "seed": 7}]}
Prints "PROGRESS k n msg" lines and one final "RESULT <json>" line.
Generated audio carries Resemble AI's imperceptible Perth watermark (applied by the library).
"""
import json
import os
import sys
import time


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    lines = job["lines"]
    n = len(lines)
    import numpy as np
    import soundfile as sf
    import torch
    torch.set_num_threads(int(job.get("threads") or max(1, (os.cpu_count() or 2) - 1)))
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    t0 = time.time()
    device = job.get("device", "cpu")
    if device == "cuda" and not torch.cuda.is_available():
        print("PROGRESS 0 %d CUDA not available - using CPU" % n, flush=True)
        device = "cpu"
    t3 = job.get("model", "v3")
    try:            # offline first: load from the local cache without contacting the Hub
        from chatterbox.mtl_tts import REPO_ID, _resolve_multilingual_t3_model
        from huggingface_hub import snapshot_download
        ckpt = snapshot_download(repo_id=REPO_ID, local_files_only=True,
                                 allow_patterns=["ve.pt", _resolve_multilingual_t3_model(t3), "s3gen.pt",
                                                 "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json"])
        model = ChatterboxMultilingualTTS.from_local(ckpt, device, t3_model=t3)
    except Exception:  # noqa: BLE001 - not cached yet: download (~3.2 GB, first use only)
        print(f"PROGRESS 0 {n} downloading Chatterbox {t3} weights into models/ (first use only)", flush=True)
        model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model=t3)
    print(f"PROGRESS 0 {n} Chatterbox {job.get('model', 'v3')} loaded in {time.time() - t0:.0f}s on {device}", flush=True)
    ex = float(job.get("exaggeration", 0.5))
    if job.get("reference"):
        model.prepare_conditionals(job["reference"], exaggeration=ex)
    results = []
    for k, L in enumerate(lines):
        seed = int(L.get("seed", 0))
        torch.manual_seed(seed)
        np.random.seed(seed % (2 ** 32))
        t1 = time.time()
        wav = model.generate(L["text"], language_id=job["language_id"], exaggeration=ex,
                             cfg_weight=float(job.get("cfg_weight", 0.5)), temperature=float(job.get("temperature", 0.8)))
        a = wav.squeeze(0).detach().cpu().numpy().astype("float32")
        tmp = L["out"] + ".tmp.wav"
        sf.write(tmp, a, model.sr, subtype="PCM_16")
        os.replace(tmp, L["out"])
        d = len(a) / model.sr
        results.append({"id": L["id"], "duration": round(d, 3), "seconds": round(time.time() - t1, 1), "seed": seed})
        print(f"PROGRESS {k + 1} {n} {L['id']}: {d:.1f}s of speech in {time.time() - t1:.0f}s", flush=True)
    print("RESULT " + json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
