# Working on FrameFoundry videos with Claude Code (optional)

FrameFoundry is a normal command-line tool. This file only tells an AI coding assistant how to help a person
plan a video and run the CLI. FrameFoundry does not need this file, Claude Code, or any AI API to work.

## Your role
You are the creative planner and operator. FrameFoundry does the production (recording, narration timing,
editing, rendering, QA). Never fabricate footage, test results or success: report what the tool reports.

## Before running anything
1. Read `README.md` (sections 6–15) and the config files in `config/`.
2. Run `./framefoundry.sh doctor`. If something essential fails, tell the user the exact fix (usually
   `./install.sh --all`) instead of working around it.
3. Run `./framefoundry.sh voices` to see whether local voice generation is installed and its licence.

## Turning a request into inputs
Example request: *"Create a promotional video for SunoSunao Events using my supplied logo, website and script.
Follow the selected marketing style, use animated subtitles, add engaging cuts and export a 16:9 YouTube video."*

* **Script** — use the user's script. If they have none, draft one in the FrameFoundry format (README §6):
  a 2–3 line hook, product/feature lines, benefits, a short call to action; 15–25 words per 6–8 s;
  mark one `*keyword*` per hook/CTA line. Show the draft and get approval before rendering.
  Only state facts the user gave you or that you verified on their website.
* **Language** — choose `--language` from the user's wording; for Hindi/Hinglish keep their spelling and
  script. For three versions use `--all-languages`; prefer writing `script.hindi.txt` / `script.hinglish.txt`
  yourself (better than machine translation), and ask a native speaker to review if possible.
* **Assets** — find the logo / images / clips the user mentions (ask only if essential), pass them with
  `--logo` and `--assets-dir`. Never use media the user is not entitled to use.
* **Website** — `--website URL`; for interactions write `--website-steps "hover X; scroll down; click Y"`.
  If it needs a login, ask the user to run `./framefoundry.sh login --url … --profile NAME` themselves
  (never handle their passwords) and then use `--browser-profile NAME`.
* **Voice** — prefer the user's recording (`--voiceover`). Otherwise local generation (Chatterbox, MIT).
  Voice cloning (`--voice-reference … --confirm-voice-permission`) only when the user confirms they have the
  speaker's permission. OmniVoice is non-commercial only; never enable it for commercial work.
* **Style** — `--style-profile generic_promo` (default) or `clean_explainer`; or learn one from the user's
  reference videos with `--reference-video` (editing parameters only, no copying of footage/branding).
  "Animated subtitles" are on by default; `--karaoke` highlights each spoken word.
* **Format** — `--aspect-ratio 16:9` for YouTube, `9:16` for reels/shorts, `1:1` for feeds.

## Running and monitoring
```bash
./generate_video.sh --script input/script.txt --language auto --website https://sunosunao.ai \
  --logo input/logo.png --assets-dir input/assets --aspect-ratio 16:9 --output sunosunao_events
```
Use `--plan-only` first for long scripts to check `scene_timeline.md` quickly. Watch the console (or
`output/<campaign>/generation.log`). Voice generation on CPU takes ~10× the speech length.

## After the run
1. Exit code 0 = every language passed QA. Read `output/<campaign>/qa_report.json` and `scene_timeline.md`.
2. For generated narration, read `project_files/voice_qa.json`; mention any line flagged as not matching
   and suggest a `{tts: …}` pronunciation fix.
3. Report: the final video path(s), duration, format, which narration/voice and licences were used, anything
   that was skipped (e.g. website recording failed, machine translation used), and next steps.
4. Small edits: change the script and re-run (cached takes and proxies make it fast), or edit
   `project_files/edl.json` and run `./framefoundry.sh rerender output/<campaign>`.
