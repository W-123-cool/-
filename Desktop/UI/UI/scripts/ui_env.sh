#!/usr/bin/env bash
UI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"

ui_resolve_ros_ws() {
  local candidate
  if [[ -n "${AI_CAR_ROS_WS:-}" && -f "${AI_CAR_ROS_WS}/car_cmd.sh" ]]; then
    echo "${AI_CAR_ROS_WS}"
    return 0
  fi
  for candidate in \
    "${HOME}/Desktop/rock_ws/ros_ws" \
    "${UI_ROOT}/../../ros_ws" \
    "${UI_ROOT}/../rock_ws/ros_ws" \
    "${HOME}/rock_ws/ros_ws"; do
    if [[ -n "${candidate}" && -f "${candidate}/car_cmd.sh" ]]; then
      echo "$(cd "${candidate}" && pwd)"
      return 0
    fi
  done
  echo "${HOME}/Desktop/rock_ws/ros_ws"
}
export AI_CAR_ROS_WS="$(ui_resolve_ros_ws)"
if [[ -z "${DISPLAY:-}" ]]; then export DISPLAY=:0; fi
export PICKUP_API_BASE="${PICKUP_API_BASE:-http://127.0.0.1:8000}"
# 车端联调：后端在 PC 时必须设 PC 局域网 IP，例如 http://192.168.1.41:8000
export COURIER_API_BASE="${COURIER_API_BASE:-${VOICE_TOUR_API_BASE:-${PICKUP_API_BASE}}}"
ui_check_python() {
  command -v python3 >/dev/null 2>&1 || { echo "错误: 未找到 python3"; return 1; }
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || { echo "错误: 需要 Python 3.8+"; return 1; }
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' && return 0
  python3 -c 'import eval_type_backport' 2>/dev/null || echo "提示: pip install --user eval_type_backport"
}
ui_check_kivy() {
  python3 -c 'import kivy' 2>/dev/null || { echo "错误: 未安装 kivy"; return 1; }
}
ui_pause_on_exit() {
  if [[ ! -t 0 ]] || [[ "${UI_KEEP_OPEN:-1}" == "1" ]]; then
    read -r -p "按 Enter 关闭窗口…" _ </dev/tty 2>/dev/null || sleep "${UI_PAUSE_SEC:-30}"
  fi
}
