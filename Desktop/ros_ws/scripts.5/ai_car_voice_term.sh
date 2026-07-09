#!/usr/bin/env bash
# 终端1 — 语音 → 大模型控车
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"

RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS="${ROS_WS}"
SHERPA_VENV="${SHERPA_VENV:-${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate}"

export CAR_CMD="${CAR_CMD:-${ROS_WS}/car_cmd.sh}"
export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export AI_CAR_LLM_PATH="${AI_CAR_LLM_PATH:-/rkllm_chat}"

clear
echo "=============================================="
echo "  终端1 — 语音 → 大模型控车"
echo "=============================================="
echo "  LLM:   ${AI_CAR_LLM_HOST}${AI_CAR_LLM_PATH}"
echo "  car_cmd: ${CAR_CMD}"
echo "  说完一句后停顿约 1 秒"
echo "=============================================="
echo ""

if [[ ! -f "${SHERPA_VENV}" ]]; then
  echo "[错误] 未找到 Sherpa venv: ${SHERPA_VENV}"
  exec bash
fi

if ! ai_car_wait_llm; then
  echo "[错误] 请先启动终端4 的 flask_server.py"
  exec bash
fi

# shellcheck source=/dev/null
source "${SHERPA_VENV}"
AUDIO_DEV="$(ai_car_detect_astra_device_or_die)"
echo "麦克风: ${AUDIO_DEV}"
echo ""

ai_car_run_stt "${AUDIO_DEV}" "${SCRIPT_DIR}/voice_to_ai_car.py"
