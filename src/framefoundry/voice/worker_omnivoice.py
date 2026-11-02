#!/usr/bin/env python3
"""FrameFoundry voice worker — OmniVoice (k2-fsa). Runs inside .venv-omnivoice.

LICENCE: OmniVoice code is Apache-2.0, but the pretrained model is CC-BY-NC (training data) and its
audio tokenizer is under the Boson Higgs Audio 2 Community License. FrameFoundry only calls this worker
when voice_config.yaml sets allow_non_commercial_models: true. Do not use the output commercially.

Usage: python worker_omnivoice.py JOB.json
JOB = {"device": "cpu", "language": null|"hi"|..., "reference": wav|null, "reference_text": str|null,
       "instruct": "male, young adult, moderate pitch"|null, "steps": 32, "guidance_scale": 2.0, "threads": N,
       "lines": [{"id": "L01", "text": "...", "out": "/abs/L01.wav", "seed": 0}]}
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
    from omnivoice import OmniVoice
    t0 = time.time()
    model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=job.get("device", "cpu"), dtype=torch.float32)
    print(f"PROGRESS 0 {n} OmniVoice loaded in {time.time() - t0:.0f}s", flush=True)
    kw = {}
    if job.get("reference"):
        kw["voice_clone_prompt"] = model.create_voice_clone_prompt(ref_audio=job["reference"], ref_text=job.get("reference_text") or None)
    elif job.get("instruct"):
        kw["instruct"] = job["instruct"]
    results = []
    for k, L in enumerate(lines):
        seed = int(L.get("seed", 0))
        torch.manual_seed(seed)
        np.random.seed(seed % (2 ** 32))
        t1 = time.time()
        audio = model.generate(text=L["text"], language=job.get("language"), num_step=int(job.get("steps", 32)),
                               guidance_scale=float(job.get("guidance_scale", 2.0)), **kw)[0]
        tmp = L["out"] + ".tmp.wav"
        sf.write(tmp, audio, model.sampling_rate, subtype="PCM_16")
        os.replace(tmp, L["out"])
        d = len(audio) / model.sampling_rate
        results.append({"id": L["id"], "duration": round(d, 3), "seconds": round(time.time() - t1, 1), "seed": seed})
        print(f"PROGRESS {k + 1} {n} {L['id']}: {d:.1f}s of speech in {time.time() - t1:.0f}s", flush=True)
    print("RESULT " + json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
