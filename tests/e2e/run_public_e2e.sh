#!/usr/bin/env bash
# End-to-end tests with public, procedurally generated fixtures only (no private media).
# Each run renders a real video and runs QA. Results: output/e2e_*/ and logs/e2e_*.log
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
F=tests/fixtures/generated
[[ -f $F/music_loop.wav ]] || .venv/bin/python tests/fixtures/make_fixtures.py
COMMON=(--background-music $F/music_loop.wav --logo $F/logo.png --assets-dir $F/assets --overwrite)
run() { local name=$1; shift; echo "=== $name: $*"; ./generate_video.sh "$@" --output "e2e_$name" > "logs/e2e_$name.log" 2>&1; echo "    exit=$? ($(grep -E 'OK |FAIL' logs/e2e_$name.log | tr -s ' ' | tr '\n' ' '))"; }
case "${1:-all}" in
  all|langs)
    run hindi_1x1 --script examples/promo_hindi.txt --language hindi --aspect-ratio 1:1 "${COMMON[@]}"
    run hinglish_9x16 --script examples/promo_hinglish.txt --language hinglish --aspect-ratio 9:16 "${COMMON[@]}"
    run romanised_auto --script examples/romanised_hindi.txt --language auto "${COMMON[@]}"
    ;;&
  all|multi)
    run all_languages --script examples/promo_english.txt --all-languages "${COMMON[@]}"
    ;;
esac
