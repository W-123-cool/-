#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/ui_env.sh"
trap 'echo ""; echo "后端异常退出"; ui_pause_on_exit' EXIT
cd "${UI_ROOT}/backend"
export MQTT_BRIDGE_ENABLED="${MQTT_BRIDGE_ENABLED:-1}"
export MQTT_ROBOT_ID="${MQTT_ROBOT_ID:-robot01}"
export MQTT_BROKER_HOST="${MQTT_BROKER_HOST:-broker.emqx.io}"
export MQTT_BROKER_PORT="${MQTT_BROKER_PORT:-1883}"
ui_check_python || { ui_pause_on_exit; exit 1; }
python3 -c 'import fastapi, uvicorn' 2>/dev/null || { echo "错误: 未安装 fastapi/uvicorn"; ui_pause_on_exit; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || \
  python3 -c 'import eval_type_backport' 2>/dev/null || { echo "错误: pip install --user eval_type_backport"; ui_pause_on_exit; exit 1; }
echo "==> 后端 http://0.0.0.0:8000 MQTT=${MQTT_BRIDGE_ENABLED} ROS_WS=${AI_CAR_ROS_WS}"
exec python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
