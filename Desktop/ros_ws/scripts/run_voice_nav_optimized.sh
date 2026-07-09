#!/usr/bin/env bash
# Voice tour one-click entry (alias for run_voice_nav_all.sh)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export AI_CAR_SKIP_CHASSIS_PROMPT="${AI_CAR_SKIP_CHASSIS_PROMPT:-0}"
exec bash "${SCRIPT_DIR}/run_voice_nav_all.sh"
