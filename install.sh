#!/usr/bin/env bash
# FrameFoundry native Linux installer.
#
# Creates isolated Python 3.12 environments inside this folder (no Docker, no Conda, no global pip),
# checks/installs FFmpeg + FFprobe and Playwright Chromium, and records everything it creates in
# .framefoundry/install_state.json so ./uninstall.sh can remove exactly that and nothing else.
#
#   ./install.sh                 core + ML extras (music separation, translation) — recommended minimum
#   ./install.sh --with-tts      + local voice generation with Chatterbox Multilingual (MIT weights)
#   ./install.sh --all           everything that is licensed for commercial use
#   ./install.sh --help          all options
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$ROOT/.framefoundry"
BIN="$STATE/bin"
LOG_DIR="$ROOT/logs"
INSTALL_LOG="$LOG_DIR/install.log"

# ---- pinned tool versions (tested) ----------------------------------------------------------------
PYTHON_VERSION="3.12"
UV_VERSION="0.13.6"                       # https://github.com/astral-sh/uv (Apache-2.0 / MIT)
FFMPEG_STATIC_BRANCH="n9.0"               # https://github.com/BtbN/FFmpeg-Builds (GPL static builds)
TORCH_VERSION="2.8.0"                     # core ML environment (Demucs, MarianMT translation)
CHATTERBOX_COMMIT="5de7a54aa4e5e2baadb0182dde554908b48b85c2"   # resemble-ai/chatterbox master, 2026-07-21 (V3 support)
OMNIVOICE_COMMIT="5ba967c4d5b0f08244ae856b033eea583d1e4517"    # k2-fsa/OmniVoice tag 0.2.1

# ---- options ----------------------------------------------------------------------------------------
WITH_ML=1; WITH_TTS=0; WITH_OMNIVOICE=0; ACCEPT_NC=0; DOWNLOAD_MODELS=0
SYSTEM_DEPS="ask"; FFMPEG_MODE="auto"; GPU="cpu"; ASSUME_YES=0; DRY_RUN=0; WITH_DEV=0

usage() {
  cat <<'EOF'
FrameFoundry installer (native Linux, no Docker)

Usage: ./install.sh [options]

Components
  --minimal                 core only: render videos from your own narration (no torch, no TTS)
  --with-tts                add local voice generation (Chatterbox Multilingual, MIT licence)
  --with-omnivoice          add OmniVoice (official k2-fsa source, pinned). Its pretrained weights are
                            CC-BY-NC (non-commercial) — requires --accept-noncommercial-models
  --accept-noncommercial-models   confirm you will only use OmniVoice for non-commercial work
  --all                     core + ML + Chatterbox TTS (everything that is commercially licensed)
  --dev                     also install test tools (pytest)
  --download-models         download model weights now instead of on first use

System
  --system-deps ask|yes|no  install missing system packages with your package manager (uses sudo).
                            default: ask (non-interactive runs: no)
  --ffmpeg auto|system|static
                            auto: use a capable system FFmpeg, otherwise download a static build
                            (BtbN GPL build, checksum-verified) into .framefoundry/
  --gpu cpu|cuda|auto       PyTorch build for the ML/TTS environments (default: cpu)
  -y, --yes                 answer yes to prompts (implies --system-deps yes)
  --dry-run                 show what would be done, change nothing
  -h, --help                this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --minimal) WITH_ML=0 ;;
    --with-tts) WITH_TTS=1 ;;
    --with-omnivoice) WITH_OMNIVOICE=1 ;;
    --accept-noncommercial-models) ACCEPT_NC=1 ;;
    --all) WITH_ML=1; WITH_TTS=1 ;;
    --dev) WITH_DEV=1 ;;
    --download-models) DOWNLOAD_MODELS=1 ;;
    --system-deps) SYSTEM_DEPS="${2:?}"; shift ;;
    --system-deps=*) SYSTEM_DEPS="${1#*=}" ;;
    --ffmpeg) FFMPEG_MODE="${2:?}"; shift ;;
    --ffmpeg=*) FFMPEG_MODE="${1#*=}" ;;
    --gpu) GPU="${2:?}"; shift ;;
    --gpu=*) GPU="${1#*=}" ;;
    -y|--yes) ASSUME_YES=1; [[ "$SYSTEM_DEPS" == "ask" ]] && SYSTEM_DEPS="yes" ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1 (see ./install.sh --help)" >&2; exit 2 ;;
  esac
  shift
done

if [[ $WITH_OMNIVOICE -eq 1 && $ACCEPT_NC -eq 0 ]]; then
  echo "ERROR: OmniVoice's pretrained weights are licensed CC-BY-NC (non-commercial only)." >&2
  echo "       Re-run with --with-omnivoice --accept-noncommercial-models if you accept that restriction." >&2
  exit 2
fi
[[ $WITH_TTS -eq 1 || $WITH_OMNIVOICE -eq 1 ]] && [[ $WITH_ML -eq 0 ]] && WITH_ML=1   # TTS QA uses the ML stack

# ---- helpers ---------------------------------------------------------------------------------------
mkdir -p "$LOG_DIR"
if [[ -t 1 ]]; then CURL_PROGRESS="--progress-bar"; else CURL_PROGRESS="-sS"; fi
if [[ $DRY_RUN -eq 0 ]]; then exec > >(tee -a "$INSTALL_LOG") 2>&1; fi
c_bold=$'\e[1m'; c_red=$'\e[31m'; c_grn=$'\e[32m'; c_yel=$'\e[33m'; c_off=$'\e[0m'
[[ -t 1 ]] || { c_bold=""; c_red=""; c_grn=""; c_yel=""; c_off=""; }
step() { echo; echo "${c_bold}==> $*${c_off}"; }
ok()   { echo "  ${c_grn}✔${c_off} $*"; }
warn() { echo "  ${c_yel}!${c_off} $*"; }
fail() { echo "  ${c_red}✘ $*${c_off}" >&2; exit 1; }
run()  { if [[ $DRY_RUN -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }
confirm() {  # confirm "question" -> 0 yes / 1 no
  [[ $ASSUME_YES -eq 1 ]] && return 0
  [[ -t 0 ]] || return 1
  read -r -p "  $1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]
}
trap 'echo; echo "${c_red}Installation failed at line $LINENO. See $INSTALL_LOG${c_off}" >&2' ERR

CREATED=()   # paths created by this installer (for uninstall)
remember() { CREATED+=("$1"); }

download() {  # download URL DEST
  if have curl; then run curl -fL --retry 3 --connect-timeout 20 $CURL_PROGRESS -o "$2" "$1"
  elif have wget; then run wget -q -O "$2" "$1"
  else fail "neither curl nor wget is installed"; fi
}

echo "${c_bold}FrameFoundry installer${c_off} — $(date '+%Y-%m-%d %H:%M:%S')"
echo "  folder: $ROOT"

# ---- 1. platform -------------------------------------------------------------------------------------
step "Detecting platform"
[[ "$(uname -s)" == "Linux" ]] || fail "FrameFoundry supports Linux only (found $(uname -s))"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH="x86_64"; FF_ARCH="linux64" ;;
  aarch64|arm64) ARCH="aarch64"; FF_ARCH="linuxarm64" ;;
  *) fail "unsupported CPU architecture: $ARCH (supported: x86_64, aarch64)" ;;
esac
DISTRO_ID="unknown"; DISTRO_LIKE=""; DISTRO_NAME="unknown"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release; DISTRO_ID="${ID:-unknown}"; DISTRO_LIKE="${ID_LIKE:-}"; DISTRO_NAME="${PRETTY_NAME:-$DISTRO_ID}"
fi
PM="none"
if have apt-get; then PM="apt"; elif have dnf; then PM="dnf"; elif have pacman; then PM="pacman"; elif have zypper; then PM="zypper"; fi
ok "$DISTRO_NAME ($ARCH), package manager: $PM"
case "$DISTRO_ID $DISTRO_LIKE" in
  *ubuntu*|*debian*|*mint*|*fedora*|*rhel*|*arch*|*manjaro*|*suse*) ;;
  *) warn "distribution '$DISTRO_ID' is not in the documented list; continuing with best effort" ;;
esac
if [[ "$GPU" == "auto" ]]; then
  if have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then GPU="cuda"; else GPU="cpu"; fi
fi
ok "PyTorch variant: $GPU"

# ---- 2. system packages ----------------------------------------------------------------------------
SUDO=""
if [[ $EUID -ne 0 ]]; then have sudo && SUDO="sudo"; fi
pm_install() {  # pm_install pkg...
  local pkgs=("$@")
  [[ ${#pkgs[@]} -eq 0 ]] && return 0
  if [[ "$SYSTEM_DEPS" == "no" ]] || [[ "$PM" == "none" ]]; then return 1; fi
  if [[ $EUID -ne 0 && -z "$SUDO" ]]; then warn "sudo is not available - cannot install: ${pkgs[*]}"; return 1; fi
  if [[ "$SYSTEM_DEPS" == "ask" ]] && ! confirm "Install with $PM (uses sudo): ${pkgs[*]} ?"; then return 1; fi
  case "$PM" in
    apt)    run $SUDO apt-get update -qq && run $SUDO apt-get install -y "${pkgs[@]}" ;;
    dnf)    run $SUDO dnf install -y "${pkgs[@]}" ;;
    pacman) run $SUDO pacman -S --needed --noconfirm "${pkgs[@]}" ;;
    zypper) run $SUDO zypper --non-interactive install "${pkgs[@]}" ;;
  esac
}

step "Checking basic tools"
MISSING=()
have curl || have wget || MISSING+=(curl)
have tar || MISSING+=(tar)
have xz || MISSING+=(xz)
have git || MISSING+=(git)
have fc-match || MISSING+=(fontconfig)
if [[ ${#MISSING[@]} -gt 0 ]]; then
  declare -A MAP_APT=([curl]=curl [tar]=tar [xz]=xz-utils [git]=git [fontconfig]=fontconfig)
  declare -A MAP_DNF=([curl]=curl [tar]=tar [xz]=xz [git]=git [fontconfig]=fontconfig)
  declare -A MAP_PAC=([curl]=curl [tar]=tar [xz]=xz [git]=git [fontconfig]=fontconfig)
  declare -A MAP_ZYP=([curl]=curl [tar]=tar [xz]=xz [git]=git [fontconfig]=fontconfig)
  PKGS=()
  for m in "${MISSING[@]}"; do
    case "$PM" in apt) PKGS+=("${MAP_APT[$m]}");; dnf) PKGS+=("${MAP_DNF[$m]}");; pacman) PKGS+=("${MAP_PAC[$m]}");; zypper) PKGS+=("${MAP_ZYP[$m]}");; esac
  done
  warn "missing: ${MISSING[*]}"
  pm_install "${PKGS[@]}" || warn "not installed automatically - install manually: ${PKGS[*]:-${MISSING[*]}}"
fi
have curl || have wget || fail "curl or wget is required"
have tar || fail "tar is required"
ok "curl/wget, tar$(have xz && echo ', xz')$(have git && echo ', git')$(have fc-match && echo ', fontconfig')"

# ---- 3. uv (isolated Python 3.12 manager) ------------------------------------------------------------
step "Python environment manager (uv $UV_VERSION)"
mkdir -p "$BIN"
export UV_PYTHON_INSTALL_DIR="$STATE/python"     # a managed Python (if needed) stays inside this folder
export UV_CACHE_DIR="$STATE/uv-cache"
export UV_PYTHON_PREFERENCE="only-managed"       # a uv-managed CPython 3.12: identical on every distro, never Conda
UV=""
if [[ -x "$BIN/uv" ]] && "$BIN/uv" --version 2>/dev/null | grep -q "$UV_VERSION"; then
  UV="$BIN/uv"; ok "found $("$UV" --version)"
else
  tgz="uv-$ARCH-unknown-linux-gnu.tar.gz"
  url="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$tgz"
  tmp="$(mktemp -d)"
  download "$url" "$tmp/$tgz"
  download "$url.sha256" "$tmp/$tgz.sha256"
  if [[ $DRY_RUN -eq 0 ]]; then
    (cd "$tmp" && sha256sum -c "$tgz.sha256" >/dev/null) || fail "uv checksum verification failed"
    tar -xzf "$tmp/$tgz" -C "$tmp"
    install -m 0755 "$tmp/uv-$ARCH-unknown-linux-gnu/uv" "$BIN/uv"
  fi
  rm -rf "$tmp"
  UV="$BIN/uv"; ok "installed uv $UV_VERSION (checksum verified) into .framefoundry/bin"
fi

# ---- 4. FFmpeg ------------------------------------------------------------------------------------------
step "FFmpeg / FFprobe"
ffmpeg_capable() {  # ffmpeg_capable /path/to/ffmpeg
  local f="$1"; [[ -x "$f" ]] || return 1
  local enc flt; enc="$("$f" -hide_banner -encoders 2>/dev/null)"; flt="$("$f" -hide_banner -filters 2>/dev/null)"
  grep -q " libx264 " <<<"$enc" && grep -qE " aac " <<<"$enc" || return 1
  for x in loudnorm sidechaincompress ebur128 blackdetect freezedetect amix; do grep -q " $x " <<<"$flt" || return 1; done
  [[ -x "$(dirname "$f")/ffprobe" ]] || have ffprobe
}
FFMPEG_SRC=""
SYS_FF="$(command -v ffmpeg || true)"
if [[ "$FFMPEG_MODE" != "static" ]]; then
  if [[ -n "$SYS_FF" ]] && ffmpeg_capable "$SYS_FF"; then
    FFMPEG_SRC="system"; ok "system FFmpeg is capable: $SYS_FF ($("$SYS_FF" -version | head -1 | cut -d' ' -f1-3))"
  else
    [[ -n "$SYS_FF" ]] && warn "system FFmpeg ($SYS_FF) lacks libx264/aac or required filters"
    case "$PM" in apt) P=ffmpeg;; dnf) P=ffmpeg;; pacman) P=ffmpeg;; zypper) P=ffmpeg-7;; *) P="";; esac
    if [[ -n "$P" && "$FFMPEG_MODE" == "auto" || "$FFMPEG_MODE" == "system" ]] && [[ -z "$SYS_FF" ]] && pm_install $P; then
      SYS_FF="$(command -v ffmpeg || true)"
      if [[ -n "$SYS_FF" ]] && ffmpeg_capable "$SYS_FF"; then FFMPEG_SRC="system"; ok "installed system FFmpeg: $SYS_FF"
      else warn "the distribution's FFmpeg package lacks H.264 (libx264) encoding (common on Fedora/openSUSE)"; fi
    fi
  fi
  [[ "$FFMPEG_MODE" == "system" && -z "$FFMPEG_SRC" ]] && fail "a capable system FFmpeg was requested but not found"
fi
if [[ -z "$FFMPEG_SRC" ]]; then
  if [[ -x "$STATE/ffmpeg/bin/ffmpeg" ]] && ffmpeg_capable "$STATE/ffmpeg/bin/ffmpeg"; then
    FFMPEG_SRC="static"; ok "static FFmpeg already present in .framefoundry/ffmpeg"
  else
    asset="ffmpeg-$FFMPEG_STATIC_BRANCH-latest-$FF_ARCH-gpl-${FFMPEG_STATIC_BRANCH#n}.tar.xz"
    base="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
    tmp="$(mktemp -d)"
    echo "  downloading $asset (static GPL build from github.com/BtbN/FFmpeg-Builds) ..."
    download "$base/$asset" "$tmp/$asset"
    download "$base/checksums.sha256" "$tmp/checksums.sha256"
    if [[ $DRY_RUN -eq 0 ]]; then
      (cd "$tmp" && grep -E "[ *]$asset\$" checksums.sha256 | sha256sum -c - >/dev/null) || fail "FFmpeg checksum verification failed"
      rm -rf "$STATE/ffmpeg"; mkdir -p "$STATE/ffmpeg"
      tar -xJf "$tmp/$asset" -C "$STATE/ffmpeg" --strip-components=1
      ffmpeg_capable "$STATE/ffmpeg/bin/ffmpeg" || fail "downloaded FFmpeg failed the capability check"
    fi
    rm -rf "$tmp"
    FFMPEG_SRC="static"; ok "static FFmpeg installed (checksum verified): $("$STATE/ffmpeg/bin/ffmpeg" -version 2>/dev/null | head -1 | cut -d' ' -f1-3)"
  fi
  run ln -sf "$STATE/ffmpeg/bin/ffmpeg" "$BIN/ffmpeg"
  run ln -sf "$STATE/ffmpeg/bin/ffprobe" "$BIN/ffprobe"
fi

# ---- 5. core Python environment -----------------------------------------------------------------------
step "Core Python $PYTHON_VERSION environment (.venv)"
venv_ok() { [[ -x "$1/bin/python" ]] && "$1/bin/python" -c "import sys; assert sys.version_info[:2]==(3,12)" 2>/dev/null; }
if venv_ok "$ROOT/.venv"; then ok "existing .venv (Python $("$ROOT/.venv/bin/python" -V | cut -d' ' -f2)) reused"
else
  run "$UV" venv --clear --python "$PYTHON_VERSION" --seed "$ROOT/.venv"
  ok "created .venv"
fi
remember ".venv"
PY="$ROOT/.venv/bin/python"
torch_index_args() {  # pip args selecting the CPU (default) or CUDA (PyPI) PyTorch build
  if [[ "$GPU" == "cuda" ]]; then echo ""
  else echo "--index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match"; fi
}
if [[ $WITH_ML -eq 1 ]]; then
  step "Core + ML packages: PyTorch $TORCH_VERSION ($GPU), Demucs, MarianMT translation"
  # shellcheck disable=SC2046
  run "$UV" pip install --python "$PY" $(torch_index_args) "torch==$TORCH_VERSION" "torchaudio==$TORCH_VERSION"
  run "$UV" pip install --python "$PY" -r "$ROOT/requirements/ml.lock-$ARCH.txt"
  ok "packages installed (requirements/ml.lock-$ARCH.txt = core + ML)"
else
  run "$UV" pip install --python "$PY" -r "$ROOT/requirements/core.lock.txt"
  ok "core packages installed (requirements/core.lock.txt)"
fi
if [[ $WITH_DEV -eq 1 ]]; then run "$UV" pip install --python "$PY" -r "$ROOT/requirements/dev.txt"; ok "dev tools installed"; fi

# ---- 6. Playwright Chromium ---------------------------------------------------------------------------
step "Playwright Chromium (website recording)"
if run "$PY" -m playwright install chromium; then ok "Chromium installed/up to date (Playwright $("$PY" -c 'import importlib.metadata as m; print(m.version("playwright"))' 2>/dev/null))"
else warn "Chromium download failed - website recording will be unavailable (uploaded recordings still work)"; fi
if [[ $DRY_RUN -eq 0 ]]; then
  if "$PY" - <<'PYEOF' 2>/dev/null
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); pg = b.new_page(); pg.set_content("<h1>ok</h1>"); assert pg.inner_text("h1") == "ok"; b.close()
PYEOF
  then ok "headless Chromium launches"
  else
    warn "headless Chromium cannot start - system libraries are missing"
    if [[ "$PM" == "apt" ]]; then
      if [[ "$SYSTEM_DEPS" != "no" ]] && ( [[ "$SYSTEM_DEPS" == "yes" ]] || confirm "Install Chromium's system libraries (playwright install-deps, uses sudo)?" ); then
        run $SUDO "$PY" -m playwright install-deps chromium || warn "install-deps failed"
      else warn "run:  sudo $PY -m playwright install-deps chromium"; fi
    else
      warn "install your distribution's packages for: nss, atk, at-spi2, cups, libdrm, libxkbcommon, libXcomposite, libXdamage, libXrandr, mesa/libgbm, pango, alsa"
    fi
  fi
fi

# ---- 7. Chatterbox TTS environment (optional) -----------------------------------------------------------
if [[ $WITH_TTS -eq 1 ]]; then
  step "Local voice generation: Chatterbox Multilingual (MIT) in .venv-tts"
  have git || fail "git is required to install Chatterbox from its official repository"
  if ! venv_ok "$ROOT/.venv-tts"; then run "$UV" venv --clear --python "$PYTHON_VERSION" --seed "$ROOT/.venv-tts"; fi
  remember ".venv-tts"
  TPY="$ROOT/.venv-tts/bin/python"
  # shellcheck disable=SC2046
  run "$UV" pip install --python "$TPY" $(torch_index_args) "torch==2.6.0" "torchaudio==2.6.0"
  run "$UV" pip install --python "$TPY" -r "$ROOT/requirements/tts-chatterbox.lock-$ARCH.txt"
  # the official source at a pinned commit; --no-deps skips its demo UI (gradio)
  run "$UV" pip install --python "$TPY" --no-deps "chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox@$CHATTERBOX_COMMIT"
  if [[ $DRY_RUN -eq 0 ]]; then
    "$TPY" -W ignore -c "import perth; from chatterbox.mtl_tts import ChatterboxMultilingualTTS, SUPPORTED_LANGUAGES; assert 'hi' in SUPPORTED_LANGUAGES and 'en' in SUPPORTED_LANGUAGES; assert perth.PerthImplicitWatermarker is not None, 'Perth watermarker unavailable'" \
      && ok "Chatterbox $CHATTERBOX_COMMIT importable (English + Hindi supported)" || fail "Chatterbox import check failed"
  fi
fi

# ---- 8. OmniVoice (optional, non-commercial weights) ---------------------------------------------------------
if [[ $WITH_OMNIVOICE -eq 1 ]]; then
  step "OmniVoice (official k2-fsa source @ ${OMNIVOICE_COMMIT:0:12}) in .venv-omnivoice — NON-COMMERCIAL weights"
  OV_SRC="$ROOT/third_party/OmniVoice"
  if [[ ! -f "$OV_SRC/pyproject.toml" ]]; then
    if [[ -d "$ROOT/.git" ]] && git -C "$ROOT" config -f .gitmodules --get submodule.third_party/OmniVoice.url >/dev/null 2>&1; then
      run git -C "$ROOT" submodule update --init third_party/OmniVoice
    else
      run git clone --filter=blob:none https://github.com/k2-fsa/OmniVoice "$OV_SRC"
      remember "third_party/OmniVoice"
    fi
  fi
  run git -C "$OV_SRC" checkout -q "$OMNIVOICE_COMMIT"
  if ! venv_ok "$ROOT/.venv-omnivoice"; then run "$UV" venv --clear --python "$PYTHON_VERSION" --seed "$ROOT/.venv-omnivoice"; fi
  remember ".venv-omnivoice"
  OPY="$ROOT/.venv-omnivoice/bin/python"
  # shellcheck disable=SC2046
  run "$UV" pip install --python "$OPY" $(torch_index_args) "torch==2.8.0" "torchaudio==2.8.0"
  run "$UV" pip install --python "$OPY" -r "$ROOT/requirements/tts-omnivoice.lock-$ARCH.txt"
  run "$UV" pip install --python "$OPY" --no-deps "$OV_SRC"
  [[ $DRY_RUN -eq 0 ]] && { "$OPY" -c "import omnivoice; from omnivoice import OmniVoice" && ok "OmniVoice importable"; }
  if [[ $DRY_RUN -eq 0 ]]; then mkdir -p "$STATE"; echo "accepted $(date -Iseconds)" > "$STATE/omnivoice_noncommercial_accepted"; fi
  warn "OmniVoice weights are CC-BY-NC: FrameFoundry will only use them when config allows non-commercial engines"
fi

# ---- 9. models (optional prefetch) ----------------------------------------------------------------------
if [[ $DOWNLOAD_MODELS -eq 1 && $DRY_RUN -eq 0 ]]; then
  step "Downloading model weights into models/"
  PYTHONPATH="$ROOT/src" "$PY" -m framefoundry models download || warn "some model downloads failed (they will be retried on first use)"
fi

# ---- 10. state + verification ---------------------------------------------------------------------------------
if [[ $DRY_RUN -eq 0 ]]; then
  mkdir -p "$STATE" "$ROOT/input" "$ROOT/output" "$ROOT/tmp" "$ROOT/logs" "$ROOT/models"
  chmod +x "$ROOT"/*.sh 2>/dev/null || true
  "$PY" - "$STATE/install_state.json" "$FFMPEG_SRC" "$GPU" "$WITH_ML" "$WITH_TTS" "$WITH_OMNIVOICE" "$ARCH" "$DISTRO_NAME" "$PM" "$UV_VERSION" "${CREATED[@]}" <<'PYEOF'
import json, sys, time, platform
out, ff, gpu, ml, tts, ov, arch, distro, pm, uv, *created = sys.argv[1:]
prev = {}
try: prev = json.load(open(out))
except Exception: pass
state = {"installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ffmpeg": ff, "torch_variant": gpu,
         "components": {"core": True, "ml": ml == "1" or prev.get("components", {}).get("ml", False),
                        "tts_chatterbox": tts == "1" or prev.get("components", {}).get("tts_chatterbox", False),
                        "tts_omnivoice": ov == "1" or prev.get("components", {}).get("tts_omnivoice", False)},
         "arch": arch, "distro": distro, "package_manager": pm, "uv": uv, "python": platform.python_version(),
         "created": sorted(set(prev.get("created", []) + created + [".framefoundry", "models", "tmp"]))}
json.dump(state, open(out, "w"), indent=1)
PYEOF
  step "Verifying installation"
  PYTHONPATH="$ROOT/src" PATH="$BIN:$PATH" "$PY" -m framefoundry doctor || warn "doctor reported problems (see above)"
fi

echo
echo "${c_grn}${c_bold}FrameFoundry is installed.${c_off}"
echo "  Generate a video:   ./generate_video.sh --script input/script.txt --voiceover input/voiceover.wav"
[[ -x "$ROOT/.venv-tts/bin/python" ]] || echo "  Local voice generation is not installed (add it with ./install.sh --with-tts)."
echo "  Help:               ./generate_video.sh --help"
