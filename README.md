# FrameFoundry — From Script to Screen

FrameFoundry is a **local, reusable video-production engine** for Linux. Give it a script and whatever you
have — a website URL, a logo, images, video clips, screen recordings, your own narration, background music,
reference videos — and it produces a professionally edited marketing video: kinetic typography, animated
subtitles, real website footage with camera moves, purposeful cuts and transitions, mixed and mastered audio,
and an automatic quality check.

It is a plain command-line application. **No Docker, no web dashboard, no server, no Anthropic/Claude API
key and no paid AI service** are needed to render a video. Optional local AI models (all running on your
machine) add voice generation, speech alignment, translation and music separation.

```
INPUT  script · logo · assets · website · reference video · voice-over · music
  ↓
ANALYSIS & PLANNING   language detection · script parsing · scene planning · style analysis
  ↓
ASSET & AUDIO PREP    website recording · voice generation / your narration · word alignment · music
  ↓
EDITING               cuts · zooms · transitions · animated subtitles · kinetic typography
  ↓
RENDER & QA           FFmpeg H.264/AAC · loudness · A/V sync · decode / black-frame checks
  ↓
OUTPUT                final_video.mp4 · 16:9 · 9:16 · 1:1 · English · Hindi · Hinglish
```

---

## 1. What it produces
Product promos, startup videos, software walkthroughs, tutorials, YouTube intros, reels/shorts and
multilingual ads (English, Hindi in Devanagari, Romanised Hindi and code-switched Hinglish).

Each run creates its own folder, e.g. `output/launch_001/`:

| File | What it is |
|---|---|
| `final_video.mp4` | the video (H.264 + AAC, 1920×1080 30 fps by default) — only named this way after QA passed |
| `qa_report.json` | every quality check with its result |
| `scene_timeline.md` | what is on screen when, and why (edit list) |
| `script.txt`, `language_report.json` | the narration actually used, language detection details |
| `voiceover.wav`, `background_music.wav`, `final_audio.wav` | processed narration, arranged music, final mix |
| `subtitles.srt/.vtt`, `subtitles_full.srt` | subtitles as burned in, and one event per line |
| `screen_recordings/` | real website recordings (`.mp4` + timing `.json` + Playwright's original `.webm`) |
| `assets/` | copies of the images/logo used |
| `project_files/` | `edl.json` (the edit), `timing.json` (word timings), `scene_plan.json`, voice takes, style profile |
| `manifest.json`, `generation.log` | inputs, versions, licences of the models used, full log |

If QA fails, the file is kept as `final_video.FAILED_QA.mp4` and the command exits with code 3 — a broken
video is never reported as a success. `--all-languages` creates `english/`, `hindi/`, `hinglish/`
sub-folders (plus `shared/` for recordings and assets) and a `summary.json`.

## 2. Supported systems
| | Status |
|---|---|
| Ubuntu 24.04 x86_64 | **tested** (see `docs/END_TO_END_TEST_REPORT.md`) |
| Debian, Linux Mint (APT) | supported by the installer (same code path as Ubuntu), not tested |
| Fedora (DNF), Arch / Manjaro (Pacman), openSUSE (Zypper) | supported by the installer, not tested. Their distro FFmpeg often lacks H.264 encoding; the installer then uses the static build |
| ARM64 (aarch64) | lock files and downloads exist for aarch64; not tested |

## 3. Hardware
* **CPU only works** (the default and the tested path): 4+ cores, 8 GB RAM (16 GB recommended for voice
  generation), ~6 GB disk for the environments plus models (Chatterbox ~3.2 GB, Whisper turbo ~1.6 GB).
* Rendering a 40 s 1080p video takes about 30–60 s on a 6-core/12-thread laptop CPU; generating narration with
  Chatterbox on CPU takes roughly 8–12× the speech duration.
* **GPU (optional, not tested):** with an NVIDIA GPU and the proprietary driver, install with
  `./install.sh --all --gpu cuda` and set `device: cuda` in `config/voice_config.yaml` and `alignment.device`
  in `config/config.yaml`. Hardware video encoders (`--encoder h264_nvenc` / `h264_vaapi`) are optional and
  **untested**: before rendering, FrameFoundry probes the chosen encoder and falls back to libx264 with a
  warning if it does not work (on the Ubuntu 24.04 test machine the static FFmpeg build's VA-API encoder needs a
  newer libva than the system has, so it fell back; NVENC needs the NVIDIA driver). libx264 on the CPU is the
  tested default and gives the best quality per bit.

## 4. Installation
```bash
git clone --recurse-submodules https://github.com/bigdadop/framefoundry.git
cd framefoundry
./install.sh --all          # core + ML extras + local voice generation (Chatterbox, MIT)
./framefoundry.sh doctor    # verify
```
Other options: `./install.sh` (core + ML, no voice model), `./install.sh --minimal` (render from your own
narration only), `--download-models` (fetch weights now), `--gpu cuda`, `--dry-run`, `--help`.

What the installer does (it never touches global Python, Conda or system settings without asking):
* creates **isolated Python 3.12 environments** inside the folder with [uv](https://github.com/astral-sh/uv)
  (uv itself is downloaded into `.framefoundry/bin` and checksum-verified): `.venv` (core),
  `.venv-tts` (Chatterbox), `.venv-omnivoice` (optional);
* uses your distribution's **FFmpeg** if it can encode H.264; otherwise it can install it with your package
  manager (asks for sudo), or downloads the official static build from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) into `.framefoundry/` (SHA-256 verified);
* installs **Playwright + Chromium** and checks that headless Chromium starts (on Debian/Ubuntu it can install
  Chromium's system libraries via `playwright install-deps`, with sudo, if they are missing);
* installs exactly the versions in `requirements/*.lock*.txt`;
* records what it created in `.framefoundry/install_state.json` for `./uninstall.sh`.

It is safe to run again (existing environments are reused). Logs: `logs/install.log`.

## 5. Dependencies
Pinned versions and licences: `requirements/` and `third_party/THIRD_PARTY_MANIFEST.md`. Highlights: Python
3.12, FFmpeg 6+ (static build n9.0.2 tested), Playwright 1.64.1 / Chromium 156, faster-whisper 1.2.1,
PyTorch 2.8 CPU, Demucs 4.1.0, transformers 5.22, Pillow 13 (with libraqm for Devanagari shaping), OpenCV 5.
Model weights are downloaded into `models/` on first use (`./framefoundry.sh models download` to prefetch).

## 6. Preparing a script
Plain text or Markdown, UTF-8, one narration line per row (see `examples/`):
```text
---
brand: Lumen Notes
tagline: Think it. Keep it.
website: https://example.com
---
# Hook
Too many ideas, and nowhere to keep them?
Notes everywhere. *Nothing* organised.
# Product
Meet Lumen Notes.
[web] Capture any idea in seconds.
[image:dashboard.jpg] Smart folders sort your notes *automatically*.
[video:demo.mp4] Share a whole project in one link.
Naam hai Lumen. {tts: नाम है Lumen}
# Call to action
Start free today.
Think it. Keep it.
```
* `# Heading` — section (hook / product / call to action drive the pacing and visual choices)
* `*word*` — emphasis: becomes the big keyword on a card and is highlighted in subtitles
* tags (optional): `[web]`, `[web:rec_01@Pricing]`, `[card]`, `[flash]`, `[logo]`, `[image:file]`, `[video:file]`
* `{tts: …}` — what the voice engine should say (pronunciation), while the subtitle shows the text as written
* numbers, ₹ amounts and domains are spoken naturally in English or Hindi ("₹2,500" → "पच्चीस सौ रुपये")
* `// comment` lines are ignored. Untagged lines are planned automatically.

FrameFoundry does **not** write marketing scripts by itself (it contains no language model). Write the
script yourself, or ask an AI assistant (see `CLAUDE.md`) to draft it.

## 7. Website recordings
```bash
./generate_video.sh --script input/script.txt --website https://example.com
./generate_video.sh --script input/script.txt --website https://example.com \
    --website-steps "hover Features; scroll down; click Pricing; scroll to FAQ"
```
FrameFoundry opens a real headless Chromium, records with a visible cursor and click ripples, logs every step
so the editor can cut to it, and follows the cursor with its virtual camera. Without `--website-steps` it does
an automatic tour: it waits for the page's real content (single-page apps often show a splash first), hovers the
main navigation, scrolls section by section, and keeps going until there is enough footage for the lines that will
show the website. Steps can also be a YAML/JSON file.
* **Read-only by default:** clicks on anything that looks like buy / pay / subscribe / upgrade / delete /
  submit / send / join / sign up / log in / generate — or that would submit a form — are skipped (hovered
  instead) unless the step is explicitly authorised *and* `browser.allow_risky_actions` is enabled.
  Password fields are never typed into.
* **Sites that need an account:** sign in yourself first, in a visible browser window:
  `./framefoundry.sh login --url https://site --profile mysite`, then add `--browser-profile mysite`.
  The session stays in `local/browser_profiles/` (private, git-ignored); delete it with
  `./framefoundry.sh login --forget mysite`. CAPTCHAs, 2FA and paywalls are never bypassed.
* `--private-text "jane@example.com"` blurs that text live while recording.
* If the website cannot be recorded, the run continues without footage (add `--require-website` to fail
  instead) — and you can supply your own capture with `--screen-recording my_capture.mp4`.
  Website footage is always a real recording, never a mock-up.

## 8. Logo and assets
`--logo logo.png` (transparent PNG recommended: used for the logo reveal, watermark and end card, never
stretched; a low-contrast logo automatically gets a light plate on the dark end card).
`--assets-dir folder/` — images (`.png .jpg .webp`) become Ken-Burns shots; videos (`.mp4 .mov .mkv .webm`)
become footage; PNGs with transparency become pop-in stickers; `.ttf/.otf` fonts are made available.
`--brand`, `--tagline`, `--handle` set the end-card text (or put them in the script's front matter).

## 9. Narration
* **Your own recording** (always works, no model needed): `--voiceover narration.wav` (any common audio
  format). It is cleaned (high-pass, loudness), long silences are trimmed, and every script word is located
  in it with Whisper so cards, subtitles and cuts land on the spoken words. `--voice-processing full` applies a
  broadcast chain (EQ, de-esser, compressor); `audio.noise_reduction: true` adds FFT denoising.
* **Local voice generation:** see section 10. **No narration:** `--no-narration` (music + text).
* For `--all-languages`, give `--voiceover-hindi`, `--voiceover-hinglish`, `--voiceover-english` as needed.

## 10. Local voice generation and licences
| Engine | Install | Languages (tested) | Weights licence | Commercial use |
|---|---|---|---|---|
| **Chatterbox Multilingual V3** (Resemble AI) — default | `./install.sh --with-tts` | English, Hindi, Hinglish (Hindi words sent in Devanagari) | MIT | **Yes** (outputs carry an imperceptible Perth watermark) |
| **OmniVoice** (k2-fsa, official source pinned as a submodule) | `./install.sh --with-omnivoice --accept-noncommercial-models` | Hindi (Romanised-Hindi script, read back correctly); English not tested | **CC-BY-NC** (model); audio tokenizer under the Boson Higgs Audio 2 Community License | **No** — only runs with `allow_non_commercial_models: true` |

Every generated line is transcribed back with Whisper and compared with the script; lines that do not match
are regenerated with a new seed (`voice_config.yaml: qa`). Results are in `project_files/voice_qa.json`.
Takes are cached, so changing one line only regenerates that line.

**Voice cloning** — `--voice-reference sample.wav --confirm-voice-permission` makes the narration sound like
the person in the sample. Only do this with that person's permission; FrameFoundry refuses without the flag.
The best 5–11 s of clear speech is selected automatically (music is removed with Demucs if detected).

## 11. Language selection
`--language auto|english|hindi|hinglish` (default auto). Detection is rule-based and explainable
(`./framefoundry.sh detect-language input/script.txt` shows the decision per sentence and word):
Devanagari Hindi, Romanised Hindi ("Sirf kuch clicks mein…") and code-switched Hinglish are distinguished,
and **Romanised Hindi is never treated as English**. Your text is never silently converted between Roman and
Devanagari script; for a single language run, add `--convert-script` if you want that. Subtitles keep your
words exactly; for the voice engine, Romanised Hindi words are converted to Devanagari internally (better
pronunciation) and English words stay in Latin script.

## 12. All three languages
```bash
./generate_video.sh --script input/script.txt --all-languages --aspect-ratio 16:9
```
creates English, Hindi and Hinglish versions with the same scenes, assets, recordings, music and style. For
each language FrameFoundry uses, in order: your own version (`input/script.hindi.txt`,
`input/script.hinglish.txt`, `input/script.english.txt`, or `--script-hindi` …); the script itself if it is
already in that language; transliteration between Hindi and Hinglish; or **local machine translation**
(Helsinki-NLP opus-mt, Apache-2.0) — marked `machine_translated` in `language_report.json` and in the log,
because marketing copy should be reviewed by a person. With Chatterbox the same narrator voice (or the same
authorised reference voice) is used for all languages.

## 13. Reference videos and style profiles
```bash
./generate_video.sh --script input/script.txt --reference-video ref1.mp4 --reference-video ref2.mp4
./framefoundry.sh analyze-reference ref1.mp4 ref2.mp4 --save-profile my_brand   # reusable profile
./generate_video.sh --script input/script.txt --style-profile my_brand
./framefoundry.sh profiles            # list;  ./framefoundry.sh profiles show generic_promo
```
Reference analysis measures editing characteristics only — cut rate, shot lengths, whip vs cut transitions,
text-card share and colours, camera motion, subtitle position, music tempo, narration pace — and turns them
into a profile. No footage, branding or audio from the reference is copied into your video.
Built-in profiles: `generic_promo` (fast, punchy; the default) and `clean_explainer` (calm, crossfades,
boxed karaoke subtitles). Your profiles live in `templates/profiles/local/` (git-ignored) and are plain YAML.
`--music-from-reference --confirm-music-rights` extracts the instrumental from a reference video you own.
`--instructions "subtitles: karaoke\ntransitions: fade\nmusic_volume_db: -8"` applies simple directives.

## 14. Formats
`--aspect-ratio 16:9` (1920×1080, default), `9:16` (1080×1920), `1:1` (1080×1080), `4:5` (1080×1350), or any
`--resolution WIDTHxHEIGHT`. `--fps` (default 30). In vertical and square formats website footage is shown in a
rounded panel over a blurred fill, so content is never cropped away or stretched. `--quality draft|standard|high`.

## 15. Where videos are saved
`output/<script-name>_NNN/final_video.mp4` (a new numbered folder per run), or `--output NAME`
(refuses to overwrite unless `--overwrite`, and only ever deletes folders FrameFoundry created).
After editing `project_files/edl.json` by hand: `./framefoundry.sh rerender output/<campaign>`.

## 16. Troubleshooting
| Problem | Fix |
|---|---|
| `FrameFoundry is not installed yet` | run `./install.sh` |
| doctor: FFmpeg lacks libx264 | `./install.sh --ffmpeg static` |
| Chromium cannot start | Ubuntu/Debian: `sudo .venv/bin/python -m playwright install-deps chromium`; other distros: install nss, atk, cups, libdrm, libxkbcommon, mesa/libgbm, pango, alsa |
| website shows a login page | `./framefoundry.sh login --url … --profile NAME`, then `--browser-profile NAME` |
| "No local voice engine is installed" | add `--voiceover file.wav`, or `./install.sh --with-tts` |
| a generated line sounds wrong | add a `{tts: …}` pronunciation (Devanagari works well for Hindi words) — see `voice_qa.json` |
| subtitles slightly off | narration differs from the script; check `project_files/voiceover_alignment.json` |
| slow | use `--quality draft` while iterating; voice takes and proxies are cached |
| QA failed | read `qa_report.json`; the video is kept as `final_video.FAILED_QA.mp4` |
Run with `--debug` for a traceback; every run writes `generation.log` in its folder.

## 17. Model and dependency licensing (summary)
FrameFoundry's code is MIT (see `LICENSE`). Default components are licensed for commercial use: Chatterbox
(MIT), Whisper/faster-whisper (MIT), Demucs (MIT), opus-mt (Apache-2.0), Playwright (Apache-2.0), FFmpeg
(LGPL/GPL, used as a program), fonts (SIL OFL). **OmniVoice weights are non-commercial (CC-BY-NC)**, and the
MMS aligner used by the original prototype (CC-BY-NC) has been removed. No weights are distributed with
FrameFoundry. Details: `third_party/THIRD_PARTY_MANIFEST.md`.

## 18. Privacy and security
Everything runs locally; nothing is uploaded anywhere (model weights are downloaded from Hugging Face and
packages from PyPI/GitHub during installation). Browser sessions, private brand profiles and your media stay in
git-ignored folders (`local/`, `input/`, `output/`). The recorder never types into password fields, never
submits forms unless you authorise it, and blurs `--private-text`. Voice cloning requires explicit
confirmation of the speaker's permission. Do not commit `output/`, `input/` or `local/` to a public repository.

## 19. Optional: working with Claude Code
`CLAUDE.md` tells Claude Code (or a similar assistant) how to help plan a video — write or improve the script,
choose assets, language and aspect ratio — and then run the normal `./generate_video.sh` and check the QA
report. FrameFoundry itself never calls an AI API and works without Claude Code.

## 20. Uninstall
`./uninstall.sh` removes the environments, tools, caches and downloaded models it created
(`--keep-models` keeps the weights, `--dry-run` shows what would go). It never deletes `input/`, `output/`,
`local/`, your configuration or the source code.

---

More: `docs/ARCHITECTURE.md` (design), `docs/END_TO_END_TEST_REPORT.md` (what was tested, with evidence),
`docs/INITIAL_PROJECT_AUDIT.md` (how this project grew out of the original SunoSunao promo pipeline).
