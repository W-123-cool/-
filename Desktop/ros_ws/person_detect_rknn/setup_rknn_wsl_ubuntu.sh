#!/usr/bin/env bash
set -euo pipefail

# One-click RKNN conversion environment setup for Ubuntu/WSL.
# Recommended Python versions for rknn-toolkit2: 3.8 ~ 3.10

WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${WORKDIR}/.venv_rknn_convert_linux"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "[INFO] Working directory: ${WORKDIR}"
echo "[INFO] Using python: ${PYTHON_BIN}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "[ERROR] ${PYTHON_BIN} not found. Install Python 3.8~3.10 first."
  exit 1
fi

PY_VER="$(${PYTHON_BIN} -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJ="${PY_VER%%.*}"
PY_MIN="${PY_VER##*.}"
if [[ "${PY_MAJ}" != "3" || "${PY_MIN}" -lt 8 || "${PY_MIN}" -gt 10 ]]; then
  echo "[ERROR] Python ${PY_VER} is not supported by RKNN conversion toolkit."
  echo "[HINT] Please install Python 3.8/3.9/3.10 in WSL, then run:"
  echo "       PYTHON_BIN=python3.10 ./setup_rknn_wsl_ubuntu.sh"
  exit 1
fi

echo "[1/5] Installing base system packages..."
sudo apt-get update
sudo apt-get install -y git python3-venv python3-pip libgl1 libglib2.0-0

echo "[2/5] Creating venv at ${VENV_DIR}..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"

echo "[3/5] Upgrading pip/setuptools/wheel..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

echo "[4/5] Installing rknn-toolkit2..."
"${VENV_DIR}/bin/python" -m pip install rknn-toolkit2

echo "[5/5] Cloning rknn_model_zoo..."
if [[ ! -d "${WORKDIR}/rknn_model_zoo" ]]; then
  git clone https://github.com/airockchip/rknn_model_zoo.git "${WORKDIR}/rknn_model_zoo"
else
  echo "[INFO] rknn_model_zoo already exists, skip clone."
fi

cat <<EOF

[DONE] RKNN conversion environment is ready.
Activate:
  source "${VENV_DIR}/bin/activate"

Check:
  python -c "from rknn.api import RKNN; print('RKNN OK')"
EOF
