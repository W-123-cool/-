#!/usr/bin/env bash
# 轻量导航栈：Nav2 + smart_switcher（逻辑对齐 Desktop/start_multi_map.sh）
# 假定 USB/MicroROS/传感器预热 已由 run_voice_nav_all / ai_car_start_stack 完成
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"

ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS="${ROS_WS}"
export AI_CAR_SCRIPT_DIR="${SCRIPT_DIR}"
MAP_PATH="${VOICE_NAV_MAP:-${ROS_WS}/install/rt_robot_nav2/share/rt_robot_nav2/map/my_map5.yaml}"
SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"
OPEN_RVIZ="${VOICE_NAV_OPEN_RVIZ:-false}"
USE_DEPTH_NAV="${VOICE_NAV_USE_DEPTH_NAV:-false}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

cleanup() {
  ai_car_cleanup_nav_stack
  exit 0
}
trap cleanup SIGINT SIGTERM

if pgrep -f "smart_switcher" >/dev/null 2>&1; then
  echo -e "${GREEN}smart_switcher 已在运行，跳过重复启动${NC}"
  exec bash
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Nav2 + smart_switcher (multi_map 对齐)${NC}"
echo -e "${GREEN}========================================${NC}"

cd "${ROS_WS}" || exit 1

if [[ "${VOICE_NAV_SKIP_BUILD:-1}" != "1" ]]; then
  echo -e "${YELLOW}编译 ros_ws…${NC}"
  colcon build || echo -e "${YELLOW}编译有警告，继续…${NC}"
fi

set +u
ai_car_prepare_ros_cli
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

if [[ "${VOICE_NAV_SKIP_USB:-1}" != "1" ]]; then
  if [[ -f "${ROS_WS}/usb_auto_setup.sh" ]]; then
    echo "${SUDO_PASS}" | sudo -S bash "${ROS_WS}/usb_auto_setup.sh" >/dev/null 2>&1 || true
  fi
fi

if [[ "${VOICE_NAV_SKIP_SENSORS:-1}" != "1" ]]; then
  ai_car_prewarm_sensors_multi_map_style
fi

if [[ ! -f "${MAP_PATH}" ]]; then
  echo -e "${RED}未找到地图: ${MAP_PATH}${NC}"
  exit 1
fi

if ai_car_lidar_running; then
  echo -e "${YELLOW}[提示] 检测到预热传感器，Nav2 启动前释放 /dev/laser …${NC}"
  ai_car_stop_prewarm_sensor_launches
fi

rm -f /tmp/smart_nav_bridge.ready "${ROS_WS}/.nav_bridge_ready" "${HOME}/.nav_bridge_ready" 2>/dev/null || true

echo -e "${YELLOW}启动 Nav2 (map=${MAP_PATH}, depth_nav=${USE_DEPTH_NAV})…${NC}"
$(ai_car_nice_prefix)ros2 launch rt_robot_nav2 rt_robot_nav2_complete.launch.py \
  use_slam:=false \
  use_nav:=true \
  map_file:="${MAP_PATH}" \
  open_rviz:="${OPEN_RVIZ}" \
  use_depth_nav:="${USE_DEPTH_NAV}" &
NAV_PID=$!

ai_car_wait_nav2_boot "${VOICE_NAV_NAV_FIXED_SEC:-10}"

echo -e "${YELLOW}启动 smart_switcher…${NC}"
$(ai_car_nice_prefix)env SMART_NAV_ACTION_WAIT_SEC="${SMART_NAV_ACTION_WAIT_SEC:-30}" \
  AI_CAR_ROS_WS="${ROS_WS}" \
  ros2 run smart_nav_manager smart_switcher &
SW_PID=$!

ai_car_wait_nav_bridge_ready "${VOICE_NAV_BRIDGE_WAIT_SEC:-25}" || true

case "${VOICE_NAV_AUTO_INITIALPOSE:-0}" in
  verify|1)
    echo -e "${YELLOW}确认 AMCL 定位…${NC}"
    ai_car_wait_amcl_active "${VOICE_NAV_INITIALPOSE_WAIT_SEC:-30}" || \
      echo -e "${YELLOW}  [提示] 未确认定位，可在 RViz 用 2D Pose Estimate${NC}"
    ;;
esac

echo -e "${GREEN}✅ 导航栈就绪${NC}"
echo -e "  MQTT: robot/nav_room  状态: robot/${VOICE_NAV_ROBOT_ID:-robot01}/status"
echo -e "  bridge markers: /tmp/smart_nav_bridge.ready ${ROS_WS}/.nav_bridge_ready"
echo -e "${YELLOW}Ctrl+C 停止本脚本（保留 MicroROS Agent / 底盘 minicom）${NC}"

wait "${NAV_PID}" 2>/dev/null || wait "${SW_PID}" 2>/dev/null || true
