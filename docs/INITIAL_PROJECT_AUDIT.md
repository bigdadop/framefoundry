# FrameFoundry — Initial Project Audit

**Date:** 2026-11-02 · **Scope:** the state of the work before FrameFoundry was built, and the decisions it led to.
(Public version: private paths, client-confidential details and development-session records are omitted;
the full audit is kept outside the repository.)

Items marked **[verified]** were checked directly during the audit. Items marked **[prior audit]** come from an
earlier audit document and were not re-run.

---

## 1. Project locations found

| Location | State |
|---|---|
| Original promo project (`videos_for_sunosunao/`) | **The only surviving, tested pipeline**: 29 scripts plus the client's media and the finished promo. Left completely untouched; a SHA-256 manifest of its 127 files was taken before any work [verified] |
| `ai-video-factory/` (FastAPI + SQLite + React dashboard prototype) | **No longer on disk** — it had been deleted before the audit [verified] |
| An earlier Docker-based `framefoundry/` attempt | **No longer on disk** — deleted before the audit; a leftover Docker image contained only a skeleton [verified] |

The source code that the two deleted prototypes had generated was recovered from local development records
for reference and kept out of the repository. Useful parts were ported into FrameFoundry (see §3).

## 2. Implemented and genuinely working (original pipeline)

| Component | Status |
|---|---|
| Finished promo video | **Works** [verified]: H.264 High 1920×1080 30 fps yuv420p, AAC-LC 48 kHz stereo, 41.17 s, integrated −13.9 LUFS, true peak −1.4 dBTP, no black segments |
| Data-driven compositor `render.py` (421 lines) + `render_parallel.py` | Worked for that video (PIL/OpenCV frames → libx264); hard-coded 1920×1080 and a Conda FFmpeg path |
| Playwright recorder rig `recorder.py` | Worked: CDP screencast → constant-30 fps MP4, native `.webm`, injected cursor, privacy blur, step marks. Did not log cursor/clicks |
| Manual login helper | Worked; it suppressed Chromium's automation flag — **not carried over** (FrameFoundry does not hide automation) |
| Voice assembly, music mix, QA scripts | Worked [prior audit] |
| Reference analysis (Demucs stems, Whisper transcripts) | Worked [prior audit] |

## 3. Partially implemented (recovered prototype code, never run during the audit)

| Component | Assessment → decision |
|---|---|
| Two diverging "generalised" renderers | one supported stills/video clips and vertical panels, the other complex-script shaping and Devanagari fonts → **merged** into `rendering/compositor.py` |
| Automatic editor (generic version of the hand-made `build_edit.py`) | kinetic cards, flash words, cursor-following camera, stills, logo reveal, subtitles, end card → **reused** (`editing/editor.py`) |
| Script format + scene planner | `# Section`, visual tags, `*emphasis*`, `{tts: …}` → **reused**; its Claude-API refinement removed |
| Website instruction parser + recording runner | read-only safety rules → **reused**, extended (cursor/click logging, form-submit protection) |
| Reference analysis + style-profile builder | → **reused**; Claude "vision notes" removed |
| Audio modules (upload, assembly, mix, music extraction, voice reference) | → **reused**; alignment matching was exact-string only → replaced by cross-script fuzzy alignment |
| Language detection, Hinglish transliteration, spoken-number normalisation | → **reused** and improved (casual romanisation, variants) |
| Dashboard orchestrator (DB-bound) | → **not reused**; replaced by a CLI pipeline |

## 4. Broken or not acceptable for the final product
* **Mandatory Anthropic API** in the prototypes (script generation, planning, step conversion, vision) → removed.
* **Paid API** (ElevenLabs provider) → removed. **Dashboard / server** (FastAPI, React, SQLite) → not carried over.
  **Docker** → not carried over.
* **Machine-specific paths:** 11 hard-coded Conda interpreter/FFmpeg paths in the original scripts [verified].
* **Campaign-specific code:** the hand-made edit list, shot list, beat arrangement and site exploration scripts.
* The music bed was exported at 192 kHz (loudness filter upsampling) [prior audit] → fixed (regression-tested).

## 5. Missing (before FrameFoundry)
Native installer/uninstaller, a CLI, automated tests, a licence, language selection, translation for three-language
output, tested 9:16 / 1:1 output, a commercially licensed default voice model and aligner, a third-party
manifest, public-repository hygiene.

## 6. Host environment [verified]
Ubuntu 24.04.4 LTS x86_64, 12-thread AMD Ryzen 5 6600H, 14 GiB RAM. NVIDIA RTX 3050 present but the NVIDIA
driver not loaded (nouveau) → **CPU is the only tested path**; AMD Radeon 680M iGPU (VA-API). No password-less
sudo (system packages could not be installed during development). No system FFmpeg; the system Python 3.12
lacked `ensurepip`. The old pipeline depended entirely on two personal Conda environments (Python 3.11).
Model caches were mostly empty (weights had been removed).

## 7. Git and GitHub [verified]
The original project had **no Git repository**, so there was no history to preserve. GitHub CLI was
authenticated as `bigdadop` (profile name "Aditya"); no public e-mail, so commits use the GitHub no-reply
address. The repository was created fresh with that identity configured locally (not globally).

## 8. Licence findings [verified against upstream, 2026-11-02]
| Component | Code | Weights / assets | Commercial use |
|---|---|---|---|
| OmniVoice 0.2.1 (k2-fsa) | Apache-2.0 | model **CC-BY-NC**; bundled audio tokenizer under the **Boson Higgs Audio 2 Community License** | **No** → optional, explicit non-commercial opt-in |
| Chatterbox Multilingual V3 (Resemble AI) | MIT | MIT (also the Hindi fine-tune) | **Yes** → default voice engine |
| torchaudio MMS forced aligner (used by the old pipeline) | BSD | **CC-BY-NC 4.0** | **No** → replaced by Whisper-based alignment |
| faster-whisper / Whisper weights | MIT | MIT | Yes |
| Demucs (original repo archived; maintained fork by the author) | MIT | MIT | Yes |
| Playwright | Apache-2.0 | — | Yes |
| Fonts (Inter, Oswald, Bebas Neue, Poppins, Sora, Teko) | SIL OFL 1.1 | — | Yes (bundled with licence texts) |
Full table: `third_party/THIRD_PARTY_MANIFEST.md`.

## 9. Risks identified
* **Private / third-party material must never be published:** the client's logos and stickers, website
  recordings (including signed-in pages), the narrator's voice sample and generated takes, third-party reference
  reels, and campaign music. All are excluded by `.gitignore` and were checked before publishing.
* The campaign-specific style profile is kept locally; a brand-neutral profile ships instead.
* Rights decisions taken for the original campaign (voice, music, non-commercial weights) are **not inherited**:
  FrameFoundry defaults to commercially licensed components and requires explicit confirmations
  (`--confirm-voice-permission`, `--confirm-music-rights`, `allow_non_commercial_models`).
* Not testable on the development machine: CUDA/NVENC, ARM64, non-Ubuntu distributions, system-package installs.

## 10. Outcome
FrameFoundry was built on these findings; what was implemented and tested — with evidence and known
limitations — is in `docs/END_TO_END_TEST_REPORT.md`; the design is in `docs/ARCHITECTURE.md`.
