#!/usr/bin/env bash
# FrameFoundry utilities:  ./framefoundry.sh <command> [options]
#   doctor | voices | profiles [list|show NAME] | analyze-reference VIDEO... --save-profile NAME
#   login --url URL --profile NAME | record --url URL | detect-language FILE | transliterate TEXT --to roman
#   models [list|download] | rerender output/<campaign> | generate ...   (./framefoundry.sh --help)
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
exec "$PY" -m framefoundry "$@"
