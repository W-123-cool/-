#!/usr/bin/env bash
# Publish /initialpose once (reads initial_pose from map yaml)
# Usage: bash scripts/publish_initial_pose_once.sh [map.yaml]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"

ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS="${ROS_WS}"
export AI_CAR_SCRIPT_DIR="${SCRIPT_DIR}"
MAP_PATH="${1:-${VOICE_NAV_MAP:-${ROS_WS}/install/rt_robot_nav2/share/rt_robot_nav2/map/my_map5.yaml}}"

ai_car_prepare_ros_cli

pose="$(ai_car_read_map_initial_pose "${MAP_PATH}")" || {
  echo "[error] no initial_pose in map: ${MAP_PATH}" >&2
  exit 1
}
read -r x y yaw <<< "${pose}"
read -r qz qw <<< "$("${AI_CAR_ROS_PYTHON}" -c "import math; y=float('${yaw}'); print(math.sin(y/2), math.cos(y/2))")"

echo "[initialpose] map=${MAP_PATH}"
echo "[initialpose] x=${x} y=${y} yaw=${yaw}"

ai_car_wait_amcl_active 45 || true
ai_car_ros2_pub_initialpose_once "${x}" "${y}" "${qz}" "${qw}"
sleep 2

if ai_car_amcl_localized; then
  echo "[initialpose] OK - AMCL localized"
  exit 0
fi

echo "[initialpose] not confirmed; use RViz 2D Pose Estimate" >&2
exit 1
