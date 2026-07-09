#!/usr/bin/env bash
# Install Sherpa Chinese KWS wake-word model (CPU).
set -euo pipefail

MODEL_ROOT="${MODEL_ROOT:-${HOME}/Desktop/rk3588-offline-bundle/model}"
TARGET_NAME="sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile"
TARGET_DIR="${MODEL_ROOT}/${TARGET_NAME}"
ARCHIVE="${TARGET_NAME}.tar.bz2"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/${ARCHIVE}"
MIRROR_URL="https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/${ARCHIVE}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROS_WS="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=========================================="
echo "  Sherpa KWS model install"
echo "=========================================="
echo "  target: ${TARGET_DIR}"
echo ""

_need_keywords() {
  [[ ! -f "${ROS_WS}/voice_nav/data/wake_keywords.txt" ]] && return 0
  if grep -q 'i.o' "${ROS_WS}/voice_nav/data/wake_keywords.txt" 2>/dev/null; then
    return 0
  fi
  return 1
}

_gen_keywords() {
  if [[ ! -f "${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate" ]]; then
    return 0
  fi
  # shellcheck source=/dev/null
  source "${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate"
  export VOICE_WAKE_MODEL_DIR="${TARGET_DIR}"
  export VOICE_WAKE_KEYWORDS_FILE="${ROS_WS}/voice_nav/data/wake_keywords.txt"
  echo "[gen] wake_keywords.txt via text2token"
  python3 "${SCRIPT_DIR}/generate_wake_keywords.py"
}

if [[ -f "${TARGET_DIR}/tokens.txt" ]] && [[ -f "${TARGET_DIR}/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx" ]]; then
  echo "[OK] model already present"
  _need_keywords && _gen_keywords || true
  exit 0
fi

mkdir -p "${MODEL_ROOT}"
cd "${MODEL_ROOT}"

if [[ ! -f "${ARCHIVE}" ]]; then
  echo "[download] ${URL}"
  if command -v wget >/dev/null 2>&1; then
    wget -c --show-progress "${URL}" -O "${ARCHIVE}" || wget -c --show-progress "${MIRROR_URL}" -O "${ARCHIVE}"
  elif command -v curl >/dev/null 2>&1; then
    curl -L --retry 3 -C - "${URL}" -o "${ARCHIVE}" || curl -L --retry 3 -C - "${MIRROR_URL}" -o "${ARCHIVE}"
  else
    echo "[error] need wget or curl" >&2
    exit 1
  fi
fi

echo "[extract] ${ARCHIVE}"
tar xf "${ARCHIVE}"

if [[ ! -f "${TARGET_DIR}/tokens.txt" ]]; then
  echo "[error] missing ${TARGET_DIR}/tokens.txt after extract" >&2
  exit 1
fi

echo "[done] KWS model installed"
ls -lh "${TARGET_DIR}" | head -12
_gen_keywords || true
echo ""
echo "export VOICE_WAKE_MODEL_DIR=${TARGET_DIR}"
