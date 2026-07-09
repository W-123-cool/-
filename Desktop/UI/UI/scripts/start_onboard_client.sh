#!/usr/bin/env bash
# Onboard UI: open web page (replaces Kivy onboard_client on vehicle).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/ui_env.sh"

ROS_ONBOARD_HELPER="${AI_CAR_ROS_WS}/scripts/onboard_api_base.sh"
if [[ -f "${ROS_ONBOARD_HELPER}" ]]; then
  # shellcheck source=/dev/null
  source "${ROS_ONBOARD_HELPER}"
  API_BASE="$(_onboard_api_base)"
else
  API_BASE="${COURIER_API_BASE:-http://127.0.0.1:8000}"
  API_BASE="${API_BASE%/}"
  _onboard_api_require() {
    if [[ "${API_BASE}" =~ ^https?://(127\.0\.0\.1|localhost)(:[0-9]+)?/?$ ]]; then
      echo "[warn] COURIER_API_BASE=${API_BASE} — vehicle localhost, not PC backend" >&2
      echo "  export COURIER_API_BASE=http://<PC_LAN_IP>:8000" >&2
      echo "  or: cp ros_ws/scripts/onboard_api.env.example ros_ws/scripts/onboard_api.env" >&2
      return 1
    fi
    return 0
  }
fi

TAB="${ONBOARD_DEFAULT_TAB:-tour}"
URL="${API_BASE}/onboard?tab=${TAB}"
export DISPLAY="${DISPLAY:-:0}"

echo "==> Onboard Web UI: ${URL}"
echo "    (PC backend: set COURIER_API_BASE or ros_ws/scripts/onboard_api.env)"

if declare -F _onboard_api_require >/dev/null 2>&1; then
  _onboard_api_require || exit 1
fi

if command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "${URL}"
elif command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser "${URL}"
elif command -v chromium >/dev/null 2>&1; then
  exec chromium "${URL}"
else
  echo "请手动在浏览器打开: ${URL}"
  exit 1
fi
