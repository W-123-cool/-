#!/usr/bin/env bash
# 终端1 — MicroROS Agent（由 start_voice_car.sh 自动打开）
MICROROS_WS="${VOICE_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"

clear
echo "=============================================="
echo "  终端1 — MicroROS Agent"
echo "=============================================="
echo ""
echo "本窗口将自动执行："
echo "  cd ${MICROROS_WS}"
echo "  source install/setup.bash"
echo "  ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888"
echo ""
echo "  保持本窗口运行，不要关闭"
echo "=============================================="
echo ""

if [[ ! -d "${MICROROS_WS}/install" ]]; then
  echo "[错误] 未找到 ${MICROROS_WS}/install"
  exec bash
fi

set +u
# shellcheck disable=SC1091
source /opt/ros/foxy/setup.bash
# shellcheck disable=SC1091
source "${MICROROS_WS}/install/setup.bash"
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

cd "${MICROROS_WS}"
exec ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
