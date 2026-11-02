#!/usr/bin/env bash
# Remove what ./install.sh created — and nothing else.
#   removed : .venv, .venv-tts, .venv-omnivoice, .framefoundry/ (uv, static FFmpeg, managed Python, caches),
#             tmp/ (render / TTS / proxy caches) and models/ (downloaded weights; keep them with --keep-models)
#   kept    : input/, output/ (your videos), local/ (private profiles, browser sessions), logs/, config/,
#             templates/, the source code, and Playwright's shared browser cache (~/.cache/ms-playwright)
#             unless --remove-browsers is given.
# Options: -y (no prompt)  --keep-models  --remove-browsers  --remove-logs  --dry-run
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
YES=0; KEEP_MODELS=0; REMOVE_BROWSERS=0; REMOVE_LOGS=0; DRY=0
for a in "$@"; do
  case "$a" in
    -y|--yes) YES=1 ;; --keep-models) KEEP_MODELS=1 ;; --remove-browsers) REMOVE_BROWSERS=1 ;;
    --remove-logs) REMOVE_LOGS=1 ;; --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
TARGETS=()
for p in .venv .venv-tts .venv-omnivoice .framefoundry tmp; do [[ -e "$ROOT/$p" ]] && TARGETS+=("$p"); done
[[ $KEEP_MODELS -eq 0 && -e "$ROOT/models" ]] && TARGETS+=("models")
[[ $REMOVE_LOGS -eq 1 && -e "$ROOT/logs" ]] && TARGETS+=("logs")
# OmniVoice source cloned by the installer outside of git (a git submodule checkout is left alone)
if [[ -f "$ROOT/.framefoundry/install_state.json" ]] && grep -q '"third_party/OmniVoice"' "$ROOT/.framefoundry/install_state.json"; then
  [[ -e "$ROOT/third_party/OmniVoice" ]] && TARGETS+=("third_party/OmniVoice")
fi
if [[ ${#TARGETS[@]} -eq 0 ]]; then echo "Nothing to remove — FrameFoundry's environments are not installed."; exit 0; fi
echo "FrameFoundry uninstall will remove (inside $ROOT):"
for p in "${TARGETS[@]}"; do echo "  - $p ($(du -sh "$ROOT/$p" 2>/dev/null | cut -f1))"; done
echo "Kept: input/, output/, local/, config/, templates/, source code$([[ $REMOVE_LOGS -eq 0 ]] && echo ', logs/')."
if [[ $DRY -eq 1 ]]; then echo "(dry run — nothing removed)"; exit 0; fi
if [[ $YES -eq 0 ]]; then read -r -p "Continue? [y/N] " ans; [[ "$ans" =~ ^[Yy] ]] || { echo "aborted"; exit 1; }; fi
if [[ $REMOVE_BROWSERS -eq 1 && -x "$ROOT/.venv/bin/python" ]]; then
  "$ROOT/.venv/bin/python" -m playwright uninstall || echo "  (could not remove Playwright browsers)"
fi
for p in "${TARGETS[@]}"; do
  case "$p" in .venv|.venv-tts|.venv-omnivoice|.framefoundry|tmp|models|logs|third_party/OmniVoice) rm -rf -- "${ROOT:?}/$p" ;; esac
done
echo "Done. Re-install any time with ./install.sh"
