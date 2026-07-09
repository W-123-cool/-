#!/usr/bin/env bash
# P1c ?????? + ?????????Nav2/switcher ???????????
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"
ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
set +u
ai_car_prepare_ros_cli
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export MQTT_ROBOT_ID="${MQTT_ROBOT_ID:-robot01}"
export MQTT_BROKER_HOST="${MQTT_BROKER_HOST:-broker.emqx.io}"
export PATROL_SNAPSHOT_URL="${PATROL_SNAPSHOT_URL:-http://127.0.0.1:8000/api/security/snapshot}"
if [[ "${PATROL_SNAPSHOT_URL}" == *"<"* ]] || [[ "${PATROL_SNAPSHOT_URL}" == *"pc_ip"* ]] || [[ "${PATROL_SNAPSHOT_URL}" == *"%3C"* ]]; then
  echo "ERROR: PATROL_SNAPSHOT_URL 仍是占位符，请设真实 PC 地址，例如：" >&2
  echo "  export PATROL_SNAPSHOT_URL=http://192.168.1.41:8000/api/security/snapshot" >&2
  exit 1
fi
export PATROL_VISION_CAMERA="${PATROL_VISION_CAMERA:-0}"
export PATROL_YOLO_MODEL="${PATROL_YOLO_MODEL:-${ROS_WS}/person_detect_rknn/yolo11n.pt}"
export PATROL_CAMERA_STREAM_PORT="${PATROL_CAMERA_STREAM_PORT:-8089}"
# optional: export PATROL_CAMERA_STREAM_URL="http://192.168.x.x:8089/stream"
echo "patrol_security: camera=${PATROL_VISION_CAMERA} stream_port=${PATROL_CAMERA_STREAM_PORT} snapshot=${PATROL_SNAPSHOT_URL}"

# 避免重复启动占端口 / 重复开相机
_stop_patrol_security() {
  pkill -f patrol_vision_node 2>/dev/null || true
  pkill -f patrol_track_assist 2>/dev/null || true
  sleep 0.5
  pkill -9 -f patrol_vision_node 2>/dev/null || true
  pkill -9 -f patrol_track_assist 2>/dev/null || true
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PATROL_CAMERA_STREAM_PORT}/tcp" 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    old_pids=$(ss -tlnp 2>/dev/null | grep ":${PATROL_CAMERA_STREAM_PORT} " | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
    for pid in ${old_pids}; do
      kill -9 "${pid}" 2>/dev/null || true
    done
  fi
}

_free_stream_port() {
  local port="$1"
  local i
  for i in 1 2 3 4 5 6; do
    if command -v ss >/dev/null 2>&1; then
      if ! ss -tln 2>/dev/null | grep -q ":${port} "; then
        return 0
      fi
    elif command -v netstat >/dev/null 2>&1; then
      if ! netstat -tln 2>/dev/null | grep -q ":${port} "; then
        return 0
      fi
    else
      sleep 1
      return 0
    fi
    _stop_patrol_security
    sleep 1
  done
  echo "ERROR: port ${port} still in use. Run: fuser -k ${port}/tcp  or  ss -tlnp | grep ${port}" >&2
  return 1
}

_stop_patrol_security
_free_stream_port "${PATROL_CAMERA_STREAM_PORT}" || exit 1

_run_patrol_bin() {
  local name="$1"
  if command -v "${name}" >/dev/null 2>&1; then
    "${name}" &
    return
  fi
  if [[ -x "${ROS_WS}/install/patrol_security/bin/${name}" ]]; then
    "${ROS_WS}/install/patrol_security/bin/${name}" &
    return
  fi
  if [[ -x "${ROS_WS}/install/patrol_security/lib/patrol_security/${name}" ]]; then
    "${ROS_WS}/install/patrol_security/lib/patrol_security/${name}" &
    return
  fi
  echo "ERROR: ${name} not found (run: colcon build --packages-select patrol_security)" >&2
  exit 1
}

_run_patrol_bin patrol_vision_node
sleep 2
if ! pgrep -f patrol_vision_node >/dev/null; then
  echo "ERROR: patrol_vision_node failed (port ${PATROL_CAMERA_STREAM_PORT} busy or camera error)" >&2
  echo "  fix: fuser -k ${PATROL_CAMERA_STREAM_PORT}/tcp ; pkill -9 -f patrol_vision_node" >&2
  exit 1
fi
_run_patrol_bin patrol_track_assist
sleep 1
if pgrep -f patrol_vision_node >/dev/null; then
  echo "patrol_vision_node: running (stream http://$(hostname -I | awk '{print $1}'):${PATROL_CAMERA_STREAM_PORT}/stream)"
else
  echo "ERROR: patrol_vision_node failed to start (check port ${PATROL_CAMERA_STREAM_PORT} or camera)" >&2
  exit 1
fi
