#!/usr/bin/env bash
# 独立终端运行 Agent；其他终端 Ctrl+C 停 Nav2/语音时不应连带退出本窗口
MICROROS_WS="${AI_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"

trap '' HUP

clear
echo "=============================================="
echo "  终端3 — MicroROS Agent"
echo "=============================================="
echo "  cd ${MICROROS_WS}"
echo "  source install/setup.bash"
echo "  ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888"
echo "  仅在本终端 Ctrl+C 才会停止 Agent"
echo "=============================================="
echo ""

if [[ ! -d "${MICROROS_WS}/install" ]]; then
  echo "[错误] 未找到 ${MICROROS_WS}/install"
  exec bash
fi

set +u
source /opt/ros/foxy/setup.bash
source "${MICROROS_WS}/install/setup.bash"
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

cd "${MICROROS_WS}"
exec ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
