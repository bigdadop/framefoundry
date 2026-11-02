# Third-party manifest

Verified against the upstream repositories, PyPI and Hugging Face model cards on **2026-11-02**.
FrameFoundry does **not** redistribute any model weights or third-party binaries: weights are downloaded by
the user's own installation into `models/`, binaries come from the distribution or from the official
release (checksum-verified). Only the SIL OFL fonts are bundled (with their licence texts), and OmniVoice
is referenced as a pinned git submodule (its source is not copied into this repository).

"Commercial status" is about *using* the component to produce videos you sell or publish for a business.
It is not legal advice — check the linked licences for your situation.

## Core components

| Name | Official URL | Version | Commit | Licence | Purpose | Model licence | Commercial status | Distribution notes |
|---|---|---|---|---|---|---|---|---|
| FFmpeg / FFprobe | https://github.com/FFmpeg/FFmpeg | system package, or static build `n9.0.2-42-gd7643a0fe3-20261101` | build from BtbN `latest` release (`ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz`, SHA-256 verified) | LGPL-2.1+ / GPL-2+ (the static build is GPL because it includes libx264) | encoding, decoding, filters, loudness, QA | — | Permitted (used as an external program) | Not bundled; installer uses the distro package or downloads the static build from https://github.com/BtbN/FFmpeg-Builds into `.framefoundry/` |
| Playwright for Python | https://github.com/microsoft/playwright-python (core: https://github.com/microsoft/playwright) | 1.64.1 (Chromium build 1248 / 156.0.8078.4) | tag `v1.64.1` = `bcb9debb4f3a` | Apache-2.0 | real website recordings | — | Permitted | Installed from PyPI; Chromium downloaded by `playwright install chromium` |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | 1.2.1 | tag `v1.2.1` = `65882eee9f5c` | MIT | transcription, word timestamps, TTS read-back QA | Whisper large-v3-turbo CT2 conversion (`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, now `dropbox-dash/…`, rev `0a363e9161cb`): **MIT** (OpenAI Whisper weights: MIT) | Permitted | Weights downloaded on first use into `models/huggingface` |
| CTranslate2 | https://github.com/OpenNMT/CTranslate2 | 4.8.4 | PyPI wheel | MIT | inference engine for faster-whisper | — | Permitted | PyPI |
| PyAV | https://github.com/PyAV-Org/PyAV | 18.1.0 (pinned: faster-whisper 1.2.1 is incompatible with PyAV 19) | PyPI wheel (bundles FFmpeg libraries, LGPL) | BSD-3-Clause | faster-whisper dependency (FrameFoundry itself decodes audio with the FFmpeg CLI) | — | Permitted | PyPI |
| Demucs | https://github.com/adefossez/demucs (the original https://github.com/facebookresearch/demucs is **archived**; maintenance continues in the author's fork) | 4.1.0 | tag `v4.1.0` = `6a604bb002d1` | MIT | music / vocal separation (music from a reference video you own; isolating a voice sample) | HTDemucs weights: MIT (same repository licence) | Permitted | Weights downloaded on first use |
| PyTorch / torchaudio | https://github.com/pytorch/pytorch | 2.8.0 (+cpu) in `.venv`; 2.6.0 in `.venv-tts` | PyTorch CPU wheel index | BSD-3-Clause | ML runtime for Demucs, translation, TTS | — | Permitted | CPU wheels from https://download.pytorch.org/whl/cpu |
| transformers + sentencepiece | https://github.com/huggingface/transformers | 5.22.1 (core), 5.2.0 (`.venv-tts`) | PyPI | Apache-2.0 | MarianMT translation | `Helsinki-NLP/opus-mt-en-hi` (rev `75d7f7c9232b`) and `opus-mt-hi-en` (rev `a7d96a16729f`): **Apache-2.0** (model-card metadata) | Permitted | Downloaded only when a translation is needed |
| OpenCV (headless) | https://github.com/opencv/opencv-python | 5.0.0.93 | PyPI | Apache-2.0 | video frame access, image processing | — | Permitted | PyPI |
| Pillow (+ libraqm, HarfBuzz, FriBiDi in the wheel) | https://github.com/python-pillow/Pillow | 13.0.0 | PyPI | MIT-CMU (HPND); bundled libraqm MIT, HarfBuzz "Old MIT", FriBiDi LGPL-2.1 | compositing, complex-script (Devanagari) text shaping | — | Permitted | PyPI |
| numpy / scipy / librosa / soundfile / PyYAML | https://numpy.org · https://scipy.org · https://github.com/librosa/librosa · https://github.com/bastibe/python-soundfile · https://github.com/yaml/pyyaml | 2.4.6 / 1.17.1 / 0.11.0 / 0.14.0 / 6.0.3 | PyPI | BSD-3 / BSD-3 / ISC / BSD-3 / MIT | numerics, audio analysis, I/O | — | Permitted | PyPI |
| uv | https://github.com/astral-sh/uv | 0.13.6 | release asset, SHA-256 verified | Apache-2.0 OR MIT | isolated Python 3.12 environments | — | Permitted | Downloaded by `install.sh` into `.framefoundry/bin` |
| CPython 3.12 (python-build-standalone) | https://github.com/astral-sh/python-build-standalone | 3.12.16 | managed by uv | PSF-2.0 | interpreter for the environments | — | Permitted | Downloaded by uv into `.framefoundry/python` |

## Voice generation

| Name | Official URL | Version | Commit | Licence | Purpose | Model licence | Commercial status | Distribution notes |
|---|---|---|---|---|---|---|---|---|
| **Chatterbox Multilingual** (Resemble AI) — default engine | https://github.com/resemble-ai/chatterbox | source at master (V3 support; PyPI 0.1.7 predates V3) | `5de7a54aa4e5e2baadb0182dde554908b48b85c2` (2026-07-21) | MIT | local TTS for English, Hindi and Hinglish; authorised voice cloning | `ResembleAI/chatterbox` (rev `5bb1f6ee58e5`, files `t3_mtl23ls_v3.safetensors`, `s3gen.pt`, `ve.pt`, `conds.pt`, tokenizer JSON): **MIT** (model card). Optional Hindi fine-tune `ResembleAI/Chatterbox-Multilingual-hi`: **MIT** | **Permitted** | Weights (~3.2 GB) downloaded by the user into `models/`. Every output carries Resemble AI's imperceptible **Perth** watermark (`resemble-perth` 1.0.1, MIT) |
| setuptools (`.venv-tts` only) | https://github.com/pypa/setuptools | 80.9.0 (pinned: provides `pkg_resources`, which the Perth watermarker imports) | PyPI | MIT | runtime dependency of resemble-perth | — | Permitted | PyPI |
| spacy-pkuseg (Chatterbox dependency) | https://github.com/explosion/spacy-pkuseg | 1.0.1 | PyPI | MIT | Chinese word segmentation inside Chatterbox's tokenizer (loaded even for en/hi) | `spacy_ontonotes` model (downloaded by the library; FrameFoundry redirects it to `models/pkuseg`) | Permitted | Not bundled |
| **OmniVoice** (k2-fsa) — optional | https://github.com/k2-fsa/OmniVoice | 0.2.1 | tag `0.2.1` = `5ba967c4d5b0f08244ae856b033eea583d1e4517` (git submodule `third_party/OmniVoice`) | Apache-2.0 (code) | optional local TTS / voice cloning | `k2-fsa/OmniVoice` model: **CC-BY-NC** ("due to constraints from its training data (e.g., Emilia)"). Bundled `audio_tokenizer/`: **Boson Higgs Audio 2 Community License** (derived from the Meta Llama 3 Community License). Text backbone: Qwen3-0.6B (Apache-2.0) | **NOT permitted for commercial use** | Never bundled. FrameFoundry only runs it when `allow_non_commercial_models: true`, and records "NON-COMMERCIAL" in the campaign manifest |
| Meta MMS forced aligner (torchaudio `MMS_FA`) — used by the old project, **removed** | https://pytorch.org/audio | — | — | BSD-2 (torchaudio) | (was: word alignment) | **CC-BY-NC 4.0** | Not permitted for commercial use | Replaced by the Whisper-based cross-script aligner |

Other Chatterbox runtime dependencies (installed from PyPI into `.venv-tts`, licences read from the installed
package metadata): s3tokenizer 0.2.0 (Apache-2.0), diffusers 0.29.0 (Apache-2.0), conformer 0.3.2 (MIT),
resemble-perth 1.0.1 (MIT), transformers 5.2.0 (Apache-2.0), torch 2.6.0 CPU (BSD-3-Clause).

## Fonts (bundled in `assets/fonts/`, licence texts alongside)

| Font | Source | Licence | Scripts |
|---|---|---|---|
| Inter (variable + italic) | https://github.com/rsms/inter | SIL OFL 1.1 (`OFL-Inter.txt`) | Latin |
| Oswald (variable) | https://github.com/googlefonts/OswaldFont | SIL OFL 1.1 (`OFL-Oswald.txt`) | Latin |
| Bebas Neue | https://github.com/dharmatype/Bebas-Neue | SIL OFL 1.1 (`OFL-BebasNeue.txt`) | Latin |
| Poppins (Medium, SemiBold, Bold) | https://github.com/itfoundry/Poppins | SIL OFL 1.1 (`OFL-Poppins.txt`) | Latin + **Devanagari** |
| Sora (variable) | https://github.com/sora-xor/sora-font | SIL OFL 1.1 (`OFL-Sora.txt`) | Latin |
| Teko (variable) | https://github.com/googlefonts/teko (file from https://github.com/google/fonts/tree/main/ofl/teko) | SIL OFL 1.1 (`OFL-Teko.txt`) | Latin + **Devanagari** |

## What is not in this repository (and must not be)
Client logos, stickers, screen recordings, narrator voice samples, reference videos, music, generated
videos, model weights, browser profiles and cookies. `.gitignore` excludes them; the publication audit
(`docs/END_TO_END_TEST_REPORT.md`) records the secret scan and the reviewed file list.
