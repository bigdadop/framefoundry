#!/usr/bin/env bash
# FrameFoundry — From Script to Screen. Generate a video from a script (+ website, assets, narration, music).
#   ./generate_video.sh --script input/script.txt --language auto --website https://example.com --aspect-ratio 16:9
#   ./generate_video.sh --help
# Runs the isolated environment created by ./install.sh — no activation, Conda or Docker needed.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "FrameFoundry is not installed yet. Run: $ROOT/install.sh" >&2
  exit 1
fi
unset PYTHONHOME
export FRAMEFOUNDRY_HOME="$ROOT" PYTHONPATH="$ROOT/src" PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1
[[ -d "$ROOT/.framefoundry/bin" ]] && export PATH="$ROOT/.framefoundry/bin:$PATH"
exec "$PY" -m framefoundry generate "$@"
