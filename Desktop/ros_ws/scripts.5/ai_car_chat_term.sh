#!/usr/bin/env bash
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LLM_DIR="${AI_CAR_LLM_DIR:-${RKSDK}/test_rkllm_run}"
VENV="${AI_CAR_LLM_VENV:-${RKSDK}/.venv/bin/activate}"

clear
echo "=============================================="
echo "  终端4 — 对话控车 aichat"
echo "=============================================="
echo "  cd ${LLM_DIR}"
echo "  source ${VENV}"
echo "  python aichat.py --yes"
echo ""
echo "  默认换算: 1米≈1秒, 90度≈4秒"
echo "=============================================="
echo ""

if [[ ! -d "${LLM_DIR}" ]]; then
  echo "[错误] 未找到 ${LLM_DIR}"
  exec bash
fi
if [[ ! -f "${VENV}" ]]; then
  echo "[错误] 未找到 venv: ${VENV}"
  exec bash
fi
if [[ ! -f "${LLM_DIR}/aichat.py" ]]; then
  echo "[错误] 未找到 ${LLM_DIR}/aichat.py"
  exec bash
fi

# shellcheck source=/dev/null
source "${VENV}"
cd "${LLM_DIR}"
exec python aichat.py --yes
