#!/usr/bin/env bash
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LLM_DIR="${AI_CAR_LLM_DIR:-${RKSDK}/test_rkllm_run}"
VENV="${AI_CAR_LLM_VENV:-${RKSDK}/.venv/bin/activate}"

clear
echo "=============================================="
echo "  终端4 — 大模型服务 flask_server"
echo "=============================================="
echo "  cd ${LLM_DIR}"
echo "  source ${VENV}"
echo "  python3 flask_server.py"
echo "  监听: http://0.0.0.0:8001/rkllm_chat"
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
if [[ ! -f "${LLM_DIR}/flask_server.py" ]]; then
  echo "[错误] 未找到 ${LLM_DIR}/flask_server.py"
  exec bash
fi

# shellcheck source=/dev/null
source "${VENV}"
cd "${LLM_DIR}"
exec python3 flask_server.py
