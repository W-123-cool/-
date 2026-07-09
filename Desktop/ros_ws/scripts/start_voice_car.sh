#!/usr/bin/env bash
# 语音控车：一键安装底盘(MicroROS+minicom) + 语音识别控车
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=voice_car_common.sh
source "${SCRIPT_DIR}/voice_car_common.sh"
VOICE_CAR_ROS_WS="$(voice_car_resolve_ros_ws "${SCRIPT_DIR}")"
export VOICE_CAR_ROS_WS
MICROROS_WS="${VOICE_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
SHERPA_BUNDLE="${SHERPA_BUNDLE:-${HOME}/Desktop/rk3588-offline-bundle}"
export VOICE_CAR_MICROROS_WS="${MICROROS_WS}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# 若未安装 Python 脚本，先安装
if [[ ! -f "${SCRIPT_DIR}/astra_voice_car.py" ]]; then
  echo -e "${YELLOW}首次运行，正在安装文件…${NC}"
  bash "${SCRIPT_DIR}/install_voice_car.sh"
fi

export DISPLAY="${DISPLAY:-:0}"
export CAR_CMD="${CAR_CMD:-${VOICE_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 语音控车（一键启动）${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}ROS 工作区: ${VOICE_CAR_ROS_WS}${NC}"
echo -e "${CYAN}car_cmd:    ${CAR_CMD}${NC}"
if [[ ! -f "${CAR_CMD}" ]]; then
  echo -e "${RED}[错误] 未找到 car_cmd.sh: ${CAR_CMD}${NC}"
  echo -e "${YELLOW}请设置: export CAR_CMD=~/Desktop/rock_ws/ros_ws/car_cmd.sh${NC}"
  exit 1
fi

# 1. Sherpa venv
VENV="${SHERPA_BUNDLE}/venv/bin/activate"
if [[ ! -f "${VENV}" ]]; then
  echo -e "${RED}[错误] 未找到 ${VENV}${NC}"
  exit 1
fi
# shellcheck source=/dev/null
source "${VENV}"

# 2. Astra 麦克风
AUDIO_DEV="$(voice_car_detect_astra_device_or_die)"
export AUDIO_DEV
echo -e "${CYAN}麦克风: ${AUDIO_DEV}${NC}"

# 3. 底盘启动（终端1 MicroROS + 终端2 minicom + usb 准备）
voice_car_start_chassis_stack "${SCRIPT_DIR}" "${MICROROS_WS}" "${VOICE_CAR_ROS_WS}"

read -r -p "底盘 RT-Thread 命令完成后，按 Enter 继续语音控车…"

# 4. ROS + car_cmd 预热
voice_car_source_ros
echo -e "${YELLOW}预热 car_cmd…${NC}"
bash "${CAR_CMD}" warmup 2>/dev/null || true

# 5. 可选：手动测试
read -r -p "是否先测试前进 2 秒? [y/N] " test_move
if [[ "${test_move,,}" == "y" || "${test_move,,}" == "yes" ]]; then
  bash "${CAR_CMD}" fwd 0.15 2 || true
  bash "${CAR_CMD}" stop || true
fi

# 6. 本终端：语音控车
echo ""
echo -e "${GREEN}语音控车已启动。说完一句后停顿约 1 秒，例如：前进一米 / 停止${NC}"
echo -e "${YELLOW}Ctrl+C 退出${NC}"
echo ""

voice_car_run_stt "${AUDIO_DEV}" "${SCRIPT_DIR}/astra_voice_car.py"
