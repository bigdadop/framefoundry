# FrameFoundry architecture

## Principles
* **One CLI, no services.** `generate_video.sh` → `python -m framefoundry generate`. Nothing listens on a port,
  so there is no `start.sh`/`stop.sh`.
* **The pipeline is data.** Every stage writes a file the next stage reads (`timing.json`, `scene_plan.json`,
  `edl.json`, `final_audio.wav`), so any stage can be inspected, edited or re-run (`framefoundry.sh rerender`).
* **Word timing drives the edit.** Cards, keywords, subtitles, footage speed-ramps and camera punch-ins are
  keyed to when each word is spoken, so re-timing the narration re-times the whole video.
* **Honest output.** Real recordings only; machine translation and non-commercial models are labelled; a video
  is named `final_video.mp4` only after QA passes.
* **Isolation.** Each heavy engine has its own virtual environment; workers are plain scripts that exchange
  JSON and WAV files with the core, so pinned versions never conflict.

## Data flow
```
script.txt ──parser──► lines ──detect──► language report ──(variant | transliteration | MT)──► lines[lang]
                                                                                   │
assets/, logo ──assets.manager──► manifest (images, video proxies, stickers, logo) │
--website ──capture.website (Playwright/Chromium, CDP screencast)──► rec.mp4 + marks/cursor/clicks
--reference-video ──reference.analyze──► analysis.json ──profile_builder──► style profile
                                                                                   ▼
narration: --voiceover ──audio.voiceover.import_voiceover──┐
           or voice.engines (Chatterbox / OmniVoice worker) ─┴► voiceover.wav + timing.json
                                    (audio.align: faster-whisper + cross-script DP alignment)
                                                                                   ▼
script.planner ──► scene_plan.json ──editing.editor──► edl.json (clips, transitions, subtitles, watermark)
                                                                                   ▼
audio.mix (arrange music, duck, master, verified AAC) ──► final_audio.wav / .m4a
rendering.render (N workers × rendering.compositor frames → libx264 segments → concat → mux)
qa.check (ffprobe + full decode) ──► qa_report.json ──► final_video.mp4 | final_video.FAILED_QA.mp4
```

## Modules (`src/framefoundry/`)
| Module | Responsibility | Origin |
|---|---|---|
| `cli.py`, `pipeline.py`, `project.py`, `config.py`, `util.py`, `doctor.py` | commands, orchestration, campaign folders, configuration, tools | new (the old orchestrator was tied to a dashboard database) |
| `script/parser.py`, `script/planner.py` | script format, scene planning | recovered dashboard planner, Claude refinement removed |
| `language/detect.py`, `hinglish.py`, `normalize.py`, `translate.py` | detection, transliteration, spoken numbers, language versions | recovered (Docker attempt) + improved; translation new |
| `capture/recorder.py`, `website.py`, `login.py` | Playwright recording rig, steps, safety rules, manual login | original SunoSunao recorder + recovered runner; cursor/click logging and form-submit safety added; automation-hiding flags removed |
| `reference/analyze.py`, `profile_builder.py` | editing-style measurement → profile | recovered, Claude vision removed |
| `assets/manager.py` | asset scan, logo prep, proxies | recovered storage logic, generalised |
| `audio/process.py`, `align.py`, `voiceover.py`, `mix.py`, `separation.py` | loudness, alignment, narration assembly/import, music, Demucs | original voice/mix chains; new Whisper cross-script aligner replaces CC-BY-NC MMS |
| `voice/engines.py`, `worker_chatterbox.py`, `worker_omnivoice.py`, `reference.py` | engine selection, licence gating, read-back QA, cloning reference | new (Chatterbox), OmniVoice worker from the original script |
| `editing/editor.py`, `styles.py`, `timeline.py` | automatic editor, style profiles, timeline doc | recovered editor (generalised `build_edit.py`) |
| `rendering/compositor.py`, `render.py` | frame compositor, parallel H.264 rendering | merge of the two recovered renderer versions (raqm shaping, panels, stills) |
| `subtitles/export.py`, `qa/check.py` | SRT/VTT, quality assurance | recovered QA, extended (A/V sync, true peak, silence, overlaps) |

## The edit decision list (`project_files/edl.json`)
Top level: `w, h, fps, duration, root, fonts, fade_out, voice_end, sting_at, watermark, subtitle_style,
clips[], transitions[], subtitles[]`. Clips are contiguous (`start`/`end` seconds):
* `card` / `flash`: `bg`, `bgword`, `items[]` (text or image, each with appearance time `t`, position, size,
  font, colour, animation `fade|pop|slam`, optional `until`, `rise`, `drift`)
* `web` / `video`: `src`, `css_w/css_h`, `remap` (clip time → source time), `cam` (`[t, cx, cy, zoom, ease]`
  keyframes), `layout full|panel`, `grade`, `overlays[]`
* `still`: `src`, `fit contain|cover`, `z0/z1`, `pan`
* `endcard`: `bg`, `items[]`, `glow_color`, `logo_plate`, `slow_zoom`
Transitions: `{at, kind: whip|punch|fade, dur, dir}`. Subtitles: `{start, end, words: [[text, emph, s, e]]}`.

## Language handling
Detection labels every word (`devanagari`, `hindi_roman`, `english`, `name`, `number`, `ambiguous`) using a
Romanised-Hindi lexicon (≈400 frequent words with common spellings), an English word list and orthographic
hints; sentences and the document are classified as english / hindi / hinglish with a variant
(romanised_hindi, code_mixed, mixed_script, devanagari_hindi). Subtitles always show the text as written.
For the voice engine, Romanised Hindi words are converted to Devanagari and numbers/currency/domains are
expanded into words of the narration language. Alignment compares phonetic keys that ignore script
(e.g. संगीत ≈ sangeet ≈ "sangit"), so recognisers answering in either script still align.

## Extending
* **New voice engine:** add an entry to `voice.engines.ENGINES` and a worker script that reads the JOB JSON
  and writes one WAV per line.
* **New visual clip type:** render it in `compositor.render_clip`, emit it from `editor.build`.
* **New transition:** handle it in `compositor.render_frame` and reference it from a style profile.
* **New language:** add it to `language_config.yaml` (Whisper/TTS language ids) and extend detection rules.
