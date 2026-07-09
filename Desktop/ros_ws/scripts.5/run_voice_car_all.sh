#!/usr/bin/env bash
# NovaJoy 语音控车 — 总入口（安装 + 底盘终端 + 语音识别控车）
# 用法:
#   bash run_voice_car_all.sh
#   bash ~/Desktop/rock_ws/ros_ws/scripts/run_voice_car_all.sh
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

_master_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

_resolve_script_dir() {
  local here candidates d
  here="$(_master_dir)"

  for d in \
    "${here}" \
    "${HOME}/Desktop/rock_ws/ros_ws/scripts" \
    "${HOME}/Desktop"; do
    if [[ -f "${d}/install_voice_car.sh" ]]; then
      echo "$(cd "${d}" && pwd)"
      return 0
    fi
  done

  echo -e "${RED}[错误] 未找到 install_voice_car.sh${NC}" >&2
  echo "请从 PC 同步 scripts 目录到:" >&2
  echo "  ~/Desktop/rock_ws/ros_ws/scripts/" >&2
  exit 1
}

SCRIPT_DIR="$(_resolve_script_dir)"
ROS_WS="${VOICE_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}"
MICROROS_WS="${VOICE_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
SHERPA_BUNDLE="${SHERPA_BUNDLE:-${HOME}/Desktop/rk3588-offline-bundle}"
DESKTOP="${UI_DESKTOP_DIR:-${HOME}/Desktop}"

export DISPLAY="${DISPLAY:-:0}"
export VOICE_CAR_MICROROS_WS="${MICROROS_WS}"
export SHERPA_BUNDLE
export CAR_CMD="${CAR_CMD:-${ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 语音控车 — 一键总启动${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}脚本目录: ${SCRIPT_DIR}${NC}"
echo -e "${CYAN}ROS 工作区: ${ROS_WS}${NC}"
echo -e "${CYAN}car_cmd:    ${CAR_CMD}${NC}"
echo ""

# 1) 安装/更新（幂等）
echo -e "${YELLOW}[1/2] 安装/更新语音控车文件…${NC}"

# 若 common 过旧，先删掉让 install 重写
COMMON="${SCRIPT_DIR}/voice_car_common.sh"
if [[ -f "${COMMON}" ]] && ! grep -q 'voice_car_resolve_ros_ws' "${COMMON}" 2>/dev/null; then
  echo -e "${YELLOW}  检测到旧版 voice_car_common.sh，将自动更新…${NC}"
  rm -f "${COMMON}"
fi

bash "${SCRIPT_DIR}/install_voice_car.sh"

# 2) 桌面快捷方式（指向本总脚本）
LAUNCHER="${DESKTOP}/NovaJoy-语音控车.sh"
cat > "${LAUNCHER}" <<LAUNCH
#!/usr/bin/env bash
export DISPLAY="\${DISPLAY:-:0}"
export CAR_CMD="${CAR_CMD}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON}"
exec bash "${SCRIPT_DIR}/run_voice_car_all.sh"
LAUNCH
chmod +x "${LAUNCHER}"

# 3) 启动（底盘终端 + 语音控车）
echo -e "${YELLOW}[2/2] 启动底盘与语音控车…${NC}"
exec bash "${SCRIPT_DIR}/start_voice_car.sh"
