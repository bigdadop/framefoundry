# FrameFoundry — End-to-End Test Report

**Test date:** 2026-11-02 · **FrameFoundry:** 1.0.0 · **Machine:** Ubuntu 24.04.4 LTS x86_64, AMD Ryzen 5 6600H
(6 cores / 12 threads), 14 GiB RAM, NVIDIA RTX 3050 present **without** the NVIDIA driver (nouveau) → every
test ran on the **CPU**. No password-less sudo (so the "no root" installer path was exercised).

Every result below comes from an actual run; paths are relative to the FrameFoundry folder (outputs are
git-ignored and not published). "Not tested" and "Blocked" are stated explicitly with the reason.

## Summary

| Test | Result | Evidence | Notes |
|---|---|---|---|
| Installation (full: core + ML + Chatterbox + dev) | **Pass** | `logs/install.log`; doctor 23/23 | no sudo: uv-managed CPython 3.12.16, static FFmpeg n9.0.2 (SHA-256 verified), Chromium 156 launched headless |
| Installation (fresh copy, `--minimal`) | **Pass** | fresh clone of the publishable files, ~1 min 42 s | nothing pre-installed; doctor 23/23 without failures (optional parts reported as warnings) |
| Installer re-run / idempotency, `--dry-run` | **Pass** | re-run with `--with-omnivoice` reused `.venv` | dry run lists actions without changes |
| Uninstall | **Pass** | fresh copy: `.venv`, `.framefoundry`, `tmp`, `models` removed | `input/` (narration) and `output/` (rendered video), config, logs and source kept |
| 16:9 rendering | **Pass** | `output/test_en_tts_16x9/final_video.mp4` | 1920×1080 30 fps H.264 High + AAC 48 kHz, 20.67 s, all QA checks passed |
| 9:16 rendering | **Pass** | `output/e2e_hinglish_9x16/`, `output/e2e_upload_hinglish_9x16/` (41.4 s, real narration), fresh-copy `fresh_upload_9x16` | 1080×1920; website footage in a square cursor-following panel (see §4) |
| 1:1 rendering | **Pass** | `output/e2e_hindi_1x1/final_video.mp4` | 1080×1080, QA passed |
| 4:5 and custom size / frame rate | **Pass** | `output/e2e_4x5/`, `output/e2e_custom_720p24/` | 1080×1350; 1280×720 at 24 fps |
| Website capture (real Chromium) | **Pass** | integration tests (local site) + live `https://sunosunao.ai` | navigation, scrolling, hover, click, `.mp4` + timing `.json` + native `.webm`; unreachable site and login wall handled |
| English subtitles | **Pass** | `test_en_tts_16x9` — 7 burned-in events, SRT/VTT | word-timed pop-in; auto box on bright backgrounds |
| Hindi subtitles (Devanagari) | **Pass** | `e2e_hindi_1x1` | raqm shaping (conjuncts, matras) with Teko / Poppins verified visually |
| Hinglish subtitles (Latin, code-mixed) | **Pass** | `e2e_hinglish_9x16`, real narration `e2e_upload_hinglish_9x16` | text kept exactly as written |
| Local voice generation — Chatterbox (MIT) | **Pass** (en / hi / Romanised hi / Hinglish) | `project_files/voice_qa.json` per run | Whisper read-back character error: see §3 |
| Local voice generation — OmniVoice (CC-BY-NC) | **Pass** (non-commercial opt-in) | `output/e2e_omnivoice_noncommercial/` | official source @ `5ba967c`; read-back error 0.024 / 0 / 0 (Romanised Hindi → Devanagari input); manifest records `commercial_use: false` + NON-COMMERCIAL note. Refused without `allow_non_commercial_models: true` (unit test) |
| Voice cloning (authorised reference) | **Pass** | `output/e2e_clone_romanised/` | reference = FrameFoundry's own generated narration (no real person's voice); similarity not measured |
| Uploaded narration | **Pass** | `fresh_upload_9x16`; real Hinglish narration (37.5 s) `e2e_upload_hinglish_9x16` | Whisper cross-script alignment matched 88.8 % of script words |
| Audio mixing | **Pass** | integration test + every render | −14.0 LUFS integrated, true peak ≤ −1.4 dBTP **measured on the AAC**, ducking, fades, 48 kHz bed |
| `--all-languages` | **Pass** | `output/e2e_all_languages/{english,hindi,hinglish}/final_video.mp4`, `summary.json` | English source; Hindi and Hinglish created by local machine translation (labelled `machine_translation`, review recommended); 488.7 s for all three on CPU (English takes cached) |
| Reference-video analysis → style profile | **Pass** | `analyze-reference` on a 37 s reference reel (17 s) | 5 cuts, 58 % text cards, subtitles at 64 % height, 112.3 BPM (matches the original manual analysis: 112.25 BPM) |
| Music separation (Demucs) | **Pass** | `logs/e2e_demucs.log` | htdemucs on CPU: a 37 s reference reel → music + vocals stems in 36.6 s |
| Manual login workflow | **Pass (plumbing)** | visible Chromium window opened with a private persistent profile (perm. 700), recording with `--browser-profile`, `--forget` | an actual sign-in needs a human and was not performed |
| GPU / hardware encoding | **Blocked / Not tested** | doctor; encoder probe | NVIDIA driver not loaded → CUDA/NVENC untestable. VA-API on the AMD iGPU **fails** with the static FFmpeg (needs a newer libva than Ubuntu 24.04 has) → automatic fallback to libx264 verified |
| Other distributions, ARM64 | **Not tested** | — | installer has APT/DNF/Pacman/Zypper and aarch64 code paths and lock files, but only Ubuntu x86_64 was available |

## 1. Automated tests
* `tests/unit` — **57 passed** (language detection incl. the four example sentences, transliteration, cross-script
  alignment, onset snapping, script parsing, scene planning, editor for 16:9 / 9:16 / 1:1, configuration, campaign-folder
  protection, CLI validation, voice-permission and licence gating, production directives, regression tests for every bug below).
* `tests/integration` — **5 passed** (≈40 s): real render + QA; mix loudness / true peak after AAC / 48 kHz bed;
  Chromium recording of a local two-page site (navigation, hover, scroll, click → new page, cursor/click log,
  native `.webm`); unreachable site fails gracefully; **a "Log in" submit button on a login wall is not clicked**.
* `pyflakes` — clean. `gitleaks` 8.30.1 — no leaks (see §7).

## 2. Installation tests (A)
| Check | Result |
|---|---|
| Distribution / package-manager detection | Ubuntu 24.04.4, APT detected |
| uv download + SHA-256 verification | pass (0.13.6) |
| Python isolation | uv-managed CPython 3.12.16 in `.venv`, `.venv-tts`, `.venv-omnivoice`; Conda base (Python 3.14) on PATH was **not** used |
| FFmpeg / FFprobe detection | no system FFmpeg → BtbN static n9.0.2 GPL build downloaded, checksum verified, capability check (libx264, aac, loudnorm, sidechaincompress, ebur128, blackdetect, freezedetect, amix) passed |
| Playwright Chromium | installed / reused (build 1248), headless launch verified |
| Chatterbox environment | torch 2.6 CPU + pinned commit `5de7a54`; import + Perth watermarker check |
| OmniVoice environment | official source via git submodule at `5ba967c` (tag 0.2.1), importable |
| Configuration loading, CLI `--help`, invalid inputs | unit tests (missing files, bad language, cloning without permission, bad resolution, non-commercial engine gated) |

## 3. Language and voice tests (C)
Detection (unit tests, `detect-language`): English → `english`; "सिर्फ कुछ क्लिक में…" → `hindi (devanagari_hindi)`;
"Sirf kuch clicks mein apne ideas ko music mein badlein." → `hinglish (romanised_hindi)` — **not English**;
"Apne ideas ko music mein convert karo, sirf kuch clicks mein." → `hinglish (code_mixed)`.

Generated narration, read back with Whisper large-v3-turbo and compared with the script (character error rate of
phonetic keys; 0 = identical; lines above 0.35 are regenerated automatically):

| Run | Language | Lines | Mean / max error | Notes |
|---|---|---|---|---|
| `test_en_tts_16x9` | English | 8 | 0.017 / 0.103 | max = "lumennotes dot app" |
| `e2e_hindi_1x1` | Hindi (Devanagari) | 8 | 0.09 / 0.44 | 7 lines ≤ 0.12; the Latin brand name inside Hindi ("मिलिए Lumen Notes से") is the weak spot → use `{tts: …}` |
| `e2e_hinglish_9x16` | Hinglish | 8 | 0.17 / 0.44 | run **before** the Romanised-Hindi detection fix (§6, #13): words like "rakhne", "jagah" were still sent in Latin |
| `e2e_romanised_auto` | Romanised Hindi (auto-detected) | 3 | 0.027 / 0.08 | |
| `e2e_clone_romanised` | Romanised Hindi, cloned voice | 3 | 0.008 / 0.024 | |
| `e2e_omnivoice_noncommercial` | Romanised Hindi (OmniVoice) | 3 | 0.008 / 0.024 | non-commercial engine |
| `e2e_all_languages/hindi` | Hindi (machine-translated) | 8 | 0.127 / 0.222 | |
| `e2e_all_languages/hinglish` | Hinglish (machine-translated, English loanwords restored) | 8 | 0.114 / 0.222 | |
| `e2e_website_sunosunao` | English | 8 | 0.049 / 0.348 | line 1 failed the first read-back and was regenerated automatically |

Word alignment of a **real** 37.5 s Hinglish narration (the original promo's voice-over) against its script:
88.8 % of script words matched across Devanagari / Latin; line starts are on average 0.11 s earlier than the
original project's (non-commercial) MMS forced alignment, mean absolute difference 0.14 s.

## 4. Rendering, editing and audio (B, E, F)
QA runs on every render (FFprobe + a complete FFmpeg decode): codec/resolution/frame rate, AAC 48 kHz, duration vs
edit, audio/video length (≤ 0.1 s) and start alignment, decode errors, unexpected black frames, frozen footage,
non-silent audio, loudness, true peak, subtitle timing (inside the video, non-overlapping), referenced assets, output
path. Measured on the passing renders: integrated loudness −14.0 LUFS, true peak −1.4 to −1.5 dBTP, audio/video length
difference ≤ 10 ms.

Visual review of contact sheets (16:9, 9:16, 1:1): kinetic cards with faded brand word and slam-in keywords, flash
words, logo reveal, Ken-Burns stills, video panels, website footage with cursor-following camera, whip transitions,
pop-in subtitles (Devanagari and Latin), end card. Issues found this way were fixed (§6 #4, #5, #22).

All rendered videos (every one passed QA; `final_video.mp4` only exists when QA passed):

| Run | Format | Duration | Loudness / true peak (AAC) | Narration | Notes |
|---|---|---|---|---|---|
| `test_en_tts_16x9` | 1920×1080 30 fps | 20.67 s | −14.0 LUFS / −1.5 dBTP | Chatterbox (en) | logo, images, video clip, music, sticker |
| `e2e_hindi_1x1` | 1080×1080 | 19.53 s | −14.0 / −1.4 | Chatterbox (hi) | Devanagari cards and subtitles |
| `e2e_hinglish_9x16` | 1080×1920 | 20.58 s | −14.0 / −1.5 | Chatterbox (hi, mixed script input) | |
| `e2e_romanised_auto` | 1920×1080 | 9.81 s | −14.0 / −1.5 | Chatterbox | language auto-detected (Romanised Hindi) |
| `e2e_clone_romanised` | 1920×1080 | 11.44 s | −14.0 / −2.3 | Chatterbox + authorised reference | |
| `e2e_omnivoice_noncommercial` | 1920×1080 | 10.80 s | −14.0 / −2.5 | OmniVoice (non-commercial) | |
| `e2e_all_languages/*` | 1920×1080 | 20.67–20.77 s | −14.0 / −1.5 | Chatterbox | three language versions |
| `e2e_website_sunosunao` | 1920×1080 | 19.81 s | −14.0 / −1.6 | Chatterbox (en) | your example command: `--language auto --website https://sunosunao.ai --aspect-ratio 16:9` |
| `e2e_upload_hinglish_9x16` | 1080×1920 | 41.37 s | −13.9 / −2.0 | **uploaded** real Hinglish narration (37.5 s) | 3 uploaded screen recordings with step marks; client logo/stickers (local only) |
| fresh copy `fresh_upload_9x16` | 1080×1920 | 20.6 s | — | uploaded narration | minimal install, then uninstalled |
| `e2e_explainer_karaoke` | 1920×1080 | 20.07 s | −14.0 / −1.5 | Chatterbox | `clean_explainer` profile: crossfades, boxed karaoke subtitles |
| `e2e_4x5` | 1080×1350 | 23.67 s | −14.8 / −1.8 | none (`--no-narration`) | 4:5 preset |
| `e2e_custom_720p24` | 1280×720 **24 fps** | 23.67 s | −14.8 / −1.8 | none | `--resolution 1280x720 --fps 24` |
| `smoke_nonarr` | 1920×1080 | 23.67 s | −15.6 / −1.2 | none | passed with a loudness *warning*; run before the re-mastering fix (§6 #24) that the two runs above already use |
| `rerender` command | — | — | — | — | re-rendered `test_en_tts_16x9` and `e2e_website_sunosunao` from their saved edit, QA passed |

Performance on this CPU: Chatterbox ≈ 8–12× the speech duration (e.g. 8 Hindi lines, 19.5 s of speech, ≈ 3 min);
Whisper read-back + alignment ≈ 55 s for 8 lines; uploaded 37.5 s narration aligned in ≈ 35 s; rendering 1080p at
≈ 1.1–1.5× real time with 8 workers; website recording ≈ the tour length (27 s) plus ≈ 10 s start-up.

## 5. Browser tests (D)
* Local test site (integration): navigation, hover, scroll, click into a second page, cursor/click logging, output files.
* Unreachable URL → `status: error`, run continues without footage (or fails with `--require-website`).
* Login wall → `status: login_required`, the "Log in" submit button is **not** clicked.
* Live site `https://sunosunao.ai` (read-only outline + automatic tour): first attempt showed the app's loading splash and only 11 s of plain scrolling → fixed (wait for real content; tour length sized from the script). Final run: 26.7 s of footage, main navigation hovered (Explore, Genres, Events, Pricing) and four sections toured; nothing clicked; video QA passed.

## 6. Bugs found by these tests and fixed
1. Installer: FFmpeg checksum line mangled by `sed` → verified with `sha256sum -c` directly.
2. Romanised-Hindi lexicon entry with a space crashed import.
3. True peak above 0 dBTP **after AAC encoding** (WAV was −0.7) → limiter + encode-measure-correct loop.
4. Dark logo invisible on the dark end card → automatic contrast plate.
5. White subtitles unreadable on bright images → automatic translucent box.
6. Chatterbox's Perth watermarker unavailable (`pkg_resources` missing in uv venvs) → `setuptools` pinned; installer now checks it.
7. Chatterbox's tokenizer downloaded a model into `~/.pkuseg` (outside FrameFoundry) → redirected to `models/pkuseg`.
8. PyAV 19 broke faster-whisper's decoder → FrameFoundry decodes with FFmpeg itself; PyAV pinned to 18.1.0.
9. Overlapping subtitle events at scene boundaries.
10. Render workers depended on the caller's `PYTHONPATH`.
11. **Safety:** the recorder clicked a "Log in" submit button → sign-in words and any form submission are now blocked.
12. Word-time estimation across pauses; final-nasal spelling (nahi/nahin) in cross-script matching.
13. Romanised Hindi words ("rakhne", "jagah", sentence-initial "Har", "Miliye", "Poora") were sent to the TTS in Latin
    script → larger lexicon, sentence-initial capitals not treated as names, Hindi-context rule (with British spellings).
14. Translation: an offline-first fallback crashed `--all-languages`; opus-mt dropped second sentences; brand names were
    translated; Hinglish romanised English words → per-language error isolation, sentence-level translation, protected
    names, phonetic restoration of English loanwords.
15. Read-back threshold too lenient (0.45 → 0.35).
16. `--instructions` directives separated by `\n` / `;`.
17. Hardware encoder crash (VA-API) → probe + automatic libx264 fallback.
18. Broken pipe (`… | head`) reported as an error.
19. Network requests for already-downloaded models → offline-first loading (Whisper, Chatterbox, translation).
20. `--plan-only` summary; generated profiles now describe their source references.
21. Sticker-overlay flag passed to the renderer as an image path (crashed a website render).
22. Uploaded recordings opened on page-load black frames (QA caught it) → leading black skipped automatically;
    9:16 website footage was too small to read → square cursor-following panel.
23. Whisper places word starts early → starts that fall in silence are snapped to the speech onset (≤ 0.35 s).
24. Music-only mixes with very peaky material ended below the loudness target after true-peak correction → one
    dynamic (true-peak-limited) re-master when the linear version loses more than 1 dB.
25. A crashed run's folder could not be overwritten (no manifest) → `generation.log` also marks FrameFoundry folders.
26. Logo plate decision: a light-lettered logo got a white plate that hid its lettering → decided by the share of
    invisible vs. white pixels; website splash screens no longer start the footage (wait for real content).
27. Subtitles over busy website text were hard to read → automatic box also on busy (high-variance) backgrounds.

## 7. Publication checks
Before the first commit:
* **Staged files reviewed:** 109 entries — source, shell scripts, configuration, docs, tests, example scripts, two
  style profiles, lock files, SIL-OFL fonts with their licence texts, and the OmniVoice **submodule pointer**
  (no upstream code copied). No media, model weights, databases, browser profiles, logs, outputs, inputs or the
  private `local/` folder (all git-ignored).
* **Secret scan:** Gitleaks 8.30.1 (`gitleaks dir`, checksum-verified official release) over exactly the files to be
  committed — *no leaks found*. A pattern search for home paths, e-mail addresses, private IPs, development-session
  ids and API-token formats found nothing.
* **Client material:** brand mentions are limited to provenance comments, test strings and the public website used
  in the example command; client logos, recordings, narration, music and reference videos stay local.
* **Identity:** `git config --local user.name "Aditya"` / `user.email 131614740+bigdadop@users.noreply.github.com`
  (the authenticated GitHub account `bigdadop`, no public e-mail → GitHub no-reply address); `git var
  GIT_AUTHOR_IDENT` and `GIT_COMMITTER_IDENT` both resolve to it; global Git configuration unchanged; no AI
  co-author or tool trailers in commit messages.
* **Original project untouched:** all 127 files of the original promo project have the same SHA-256 as before
  the work started.
* There was no previous Git history to preserve; nothing is force-pushed.

After the first commit and push:
* `gitleaks git` over the committed history: 1 commit scanned, *no leaks found*; `git interpret-trailers` finds no
  trailers in the commit message.
* Public repository `https://github.com/bigdadop/framefoundry` created under the authenticated account (owner
  `bigdadop`), pushed with a normal `git push -u origin main` to the new, empty repository.
* GitHub API: commit `9ef0476` on `main`, author and committer `Aditya <131614740+bigdadop@users.noreply.github.com>`,
  linked by GitHub to the login `bigdadop`; `third_party/OmniVoice` is a submodule pointing at
  `https://github.com/k2-fsa/OmniVoice` @ `5ba967c4d5b0f08244ae856b033eea583d1e4517`.
* Follow-up commit: `LICENSE` reduced to the standard MIT text so GitHub detects the licence (the third-party
  licence note lives in the README and `third_party/THIRD_PARTY_MANIFEST.md`).

## 8. Not tested / limitations
* CUDA, NVENC and VA-API hardware paths (driver / libva limits above), ARM64, and distributions other than Ubuntu 24.04.
* System-package installation through sudo (no password-less sudo on the test machine) — the static FFmpeg path was used.
* Voice-clone **similarity** was not measured (no commercially licensed speaker-verification model is bundled).
* Machine translation (opus-mt) is functional but its marketing copy needs human review — it is labelled as such.
* An actual interactive website sign-in (needs a human).
