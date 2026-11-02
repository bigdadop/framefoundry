# FrameFoundry tests

```bash
./install.sh --all --dev                       # pytest + pyflakes are in requirements/dev.txt
.venv/bin/python -m pytest tests/unit          # fast: language, alignment, script, editor, config, CLI (no media)
PATH=.framefoundry/bin:$PATH .venv/bin/python -m pytest tests/integration
                                               # real FFmpeg renders + QA, audio mix/true-peak, Chromium recordings
                                               # of a local test site (navigation, scroll, click, login-wall safety)
tests/e2e/run_public_e2e.sh [langs|multi|all]  # full videos with voice generation (needs ./install.sh --with-tts)
```

All test media is generated procedurally by `tests/fixtures/make_fixtures.py` (a fictional "Lumen Notes"
brand, product-style images, a transparent cut-out, an FFmpeg-generated clip, an original synthesised music
loop and a two-page local website). Nothing private or copyrighted is used, and the generated files are not
committed (`tests/fixtures/generated/` is git-ignored).

Each end-to-end run writes `output/e2e_<name>/` (with `qa_report.json`, `project_files/voice_qa.json`) and
`logs/e2e_<name>.log`. The results of the last full test campaign are in `docs/END_TO_END_TEST_REPORT.md`.
