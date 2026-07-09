#!/usr/bin/env bash
# 兼容旧名：转发到总入口
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "${DIR}/run_voice_car_all.sh"
