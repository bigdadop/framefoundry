#!/usr/bin/env bash
# Regenerate requirements/*.lock*.txt from requirements/*.in (maintainer tool; needs uv).
#   core      : universal lock (no torch)
#   ml / tts  : per-architecture locks resolved against the PyTorch CPU index; torch/torchaudio
#               themselves are omitted (install.sh installs the right CPU/CUDA build first).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="${UV:-$ROOT/.framefoundry/bin/uv}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.framefoundry/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$ROOT/.framefoundry/python}"
export UV_PYTHON_PREFERENCE=only-managed
R="$ROOT/requirements"
common=(--python-version 3.12 --no-header --quiet)
torch_idx=(--index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
           --index-strategy unsafe-best-match --no-emit-package torch --no-emit-package torchaudio)

"$UV" pip compile "$R/core.in" --universal "${common[@]}" -o "$R/core.lock.txt"
for arch in x86_64 aarch64; do
  for name in ml tts-chatterbox tts-omnivoice; do
    "$UV" pip compile "$R/$name.in" "${common[@]}" --python-platform "$arch-manylinux_2_28" "${torch_idx[@]}" \
      -o "$R/$name.lock-$arch.txt"
  done
done
echo "locks written:"; ls -1 "$R"/*.lock*.txt
