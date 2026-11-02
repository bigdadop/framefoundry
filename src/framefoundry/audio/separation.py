"""Music / vocal separation with Demucs (htdemucs, MIT) — maintained upstream at github.com/adefossez/demucs.

Used for (a) extracting the instrumental bed from a reference video when you own / are licensed to
reuse that music, and (b) isolating a speaker's voice from background music in a voice reference.
"""
import os
import shutil
import tempfile

import soundfile as sf

from framefoundry.util import FrameFoundryError, cpu_count, ffmpeg, info


def separate(src, out_dir, want=("music", "vocals")):
    """-> {'music': path, 'vocals': path} (44.1 kHz stereo float WAV)."""
    try:
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
    except ImportError:
        raise FrameFoundryError("Demucs is not installed — run ./install.sh (without --minimal) to enable separation") from None
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=out_dir)
    try:
        wav = os.path.join(tmp, "a.wav")
        ffmpeg("-i", src, "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_f32le", wav)
        info("separating music and voice with Demucs (htdemucs) — first use downloads the model")
        torch.set_num_threads(max(1, cpu_count() - 1))
        model = get_model("htdemucs")
        model.eval()
        y, sr = sf.read(wav, dtype="float32", always_2d=True)
        x = torch.from_numpy(y.T.copy())
        ref = x.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        with torch.no_grad():
            s = apply_model(model, ((x - mean) / std)[None], shifts=1, split=True, overlap=0.25, progress=False, num_workers=0)[0]
        s = s * std + mean
        voc = s[model.sources.index("vocals")]
        out = {}
        if "music" in want:
            out["music"] = os.path.join(out_dir, "music.wav")
            sf.write(out["music"], (s.sum(0) - voc).T.numpy(), sr, subtype="FLOAT")
        if "vocals" in want:
            out["vocals"] = os.path.join(out_dir, "vocals.wav")
            sf.write(out["vocals"], voc.T.numpy(), sr, subtype="FLOAT")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
