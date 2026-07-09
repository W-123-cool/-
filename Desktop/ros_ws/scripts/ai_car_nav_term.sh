#!/usr/bin/env bash
# 终端5 — 轻量导航栈（Nav2 + switcher）；强制系统 Python，不用 Sherpa venv
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"
export AI_CAR_SCRIPT_DIR="${SCRIPT_DIR}"
export DISPLAY="${DISPLAY:-:0}"
ai_car_prepare_ros_cli
ai_car_verify_system_numpy || true
exec bash "${SCRIPT_DIR}/start_nav_stack_light.sh"
