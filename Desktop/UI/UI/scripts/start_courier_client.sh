#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/ui_env.sh"
trap 'echo ""; echo "送货端异常退出"; ui_pause_on_exit' EXIT
cd "${UI_ROOT}"
ui_check_python || { ui_pause_on_exit; exit 1; }
ui_check_kivy || { ui_pause_on_exit; exit 1; }
echo "==> 送货端 DISPLAY=${DISPLAY} API=${COURIER_API_BASE}"
exec python3 -m courier_client.main
