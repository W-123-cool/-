#!/usr/bin/env bash
# NovaJoy 大模型控车 — 唯一总入口（安装 + 四终端 + 语音）
# 用法: bash ~/Desktop/rock_ws/ros_ws/scripts/run_ai_car_all.sh
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

_resolve_script_dir() {
  local here d
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  for d in "${here}" "${HOME}/Desktop/rock_ws/ros_ws/scripts" "${HOME}/Desktop"; do
    if [[ -f "${d}/start_ai_car.sh" ]]; then
      echo "$(cd "${d}" && pwd)"
      return 0
    fi
  done
  echo -e "${RED}[错误] 未找到 start_ai_car.sh${NC}" >&2
  echo "请同步 scripts 或运行: bash ~/Desktop/bootstrap_ai_car_all.sh --gen-only" >&2
  exit 1
}

SCRIPT_DIR="$(_resolve_script_dir)"

# 必须先去掉 CRLF，否则 source ai_car_common.sh 会报 $'\r': command not found
if [[ -f "${SCRIPT_DIR}/fix_scripts_crlf.sh" ]]; then
  bash "${SCRIPT_DIR}/fix_scripts_crlf.sh" >/dev/null 2>&1 || true
else
  for _f in "${SCRIPT_DIR}"/*.sh; do
    [[ -f "${_f}" ]] || continue
    grep -q $'\r' "${_f}" 2>/dev/null || continue
    sed -i 's/\r$//' "${_f}" 2>/dev/null || { tr -d '\r' < "${_f}" > "${_f}.lf" && mv "${_f}.lf" "${_f}"; }
  done
fi

export DISPLAY="${DISPLAY:-:0}"
export AI_CAR_RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
export AI_CAR_LLM_DIR="${AI_CAR_LLM_DIR:-${AI_CAR_RKSDK}/test_rkllm_run}"
export AI_CAR_MICROROS_WS="${AI_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
export AI_CAR_ROS_WS="${AI_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}"
export CAR_CMD="${CAR_CMD:-${AI_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"
export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export AI_CAR_LLM_PATH="${AI_CAR_LLM_PATH:-/rkllm_chat}"
export AI_CAR_FLASK_URL="${AI_CAR_FLASK_URL:-${AI_CAR_LLM_HOST}${AI_CAR_LLM_PATH}}"
export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"
export AI_CAR_AUTO_CHASSIS="${AI_CAR_AUTO_CHASSIS:-0}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 大模型控车 — 一键总启动${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}脚本: ${SCRIPT_DIR}${NC}"
echo -e "${CYAN}终端: 1=语音  2=底盘  3=MicroROS  4=flask${NC}"
echo ""

echo -e "${YELLOW}[1/2] 安装…${NC}"
bash "${SCRIPT_DIR}/install_ai_car.sh"

if [[ ! -f "${SCRIPT_DIR}/start_ai_car.sh" ]]; then
  echo -e "${RED}[错误] 缺少 start_ai_car.sh，请运行 bootstrap_ai_car_all.sh${NC}"
  exit 1
fi

echo -e "${YELLOW}[2/2] 启动四终端 + 本窗口语音…${NC}"
exec bash "${SCRIPT_DIR}/start_ai_car.sh"
