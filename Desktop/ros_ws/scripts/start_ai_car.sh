#!/usr/bin/env bash
# 大模型控车 + 语音 — 总启动（自动开终端2/3/4，本终端=终端1语音）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"

AI_CAR_ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS
MICROROS_WS="${AI_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LLM_DIR="${AI_CAR_LLM_DIR:-${RKSDK}/test_rkllm_run}"
SHERPA_VENV="${SHERPA_VENV:-${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

export DISPLAY="${DISPLAY:-:0}"
export CAR_CMD="${CAR_CMD:-${AI_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"
export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"
export AI_CAR_AUTO_CHASSIS="${AI_CAR_AUTO_CHASSIS:-0}"
export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export AI_CAR_LLM_PATH="${AI_CAR_LLM_PATH:-/rkllm_chat}"
export AI_CAR_FLASK_URL="${AI_CAR_FLASK_URL:-${AI_CAR_LLM_HOST}${AI_CAR_LLM_PATH}}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 大模型控车 — 四终端一键启动${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}终端1(本窗口): 语音 → LLM → car_cmd${NC}"
echo -e "${CYAN}终端2:         USB + minicom 底盘${NC}"
echo -e "${CYAN}终端3:         MicroROS Agent :8888${NC}"
echo -e "${CYAN}终端4:         flask_server :8001${NC}"
echo -e "${CYAN}ROS:   ${AI_CAR_ROS_WS}${NC}"
echo -e "${CYAN}RKSDK: ${RKSDK}${NC}"

ai_car_start_stack "${SCRIPT_DIR}" "${MICROROS_WS}" "${AI_CAR_ROS_WS}" "${RKSDK}" "${LLM_DIR}"

ai_car_source_ros
if [[ -f "${CAR_CMD}" ]]; then
  echo -e "${YELLOW}预热 car_cmd…${NC}"
  bash "${CAR_CMD}" warmup 2>/dev/null || true
fi

if [[ ! -f "${SHERPA_VENV}" ]]; then
  echo -e "${RED}[错误] 未找到 Sherpa venv: ${SHERPA_VENV}${NC}"
  exit 1
fi

if ! ai_car_wait_llm; then
  echo -e "${RED}[错误] 大模型未就绪。请先等终端4 出现 rkllm init success / Running on :8001${NC}"
  exit 1
fi

# shellcheck source=/dev/null
source "${SHERPA_VENV}"

AUDIO_DEV="$(ai_car_detect_astra_device_or_die)"

echo ""
echo -e "${GREEN}终端1 — 语音输入 → 大模型控车${NC}"
echo -e "${CYAN}麦克风: ${AUDIO_DEV}${NC}"
echo -e "${CYAN}LLM:    ${AI_CAR_LLM_HOST}${AI_CAR_LLM_PATH}${NC}"
echo -e "${YELLOW}说完一句后停顿约 1 秒；Ctrl+C 退出${NC}"
echo ""

ai_car_run_stt "${AUDIO_DEV}" "${SCRIPT_DIR}/voice_to_ai_car.py"
