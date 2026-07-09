#!/usr/bin/env bash
# 大模型控车 — 安装（复制脚本、桌面快捷方式）
set -eu
if [[ -n "${BASH_VERSION:-}" ]]; then
  set -o pipefail 2>/dev/null || true
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 加载 common 前修复 CRLF
for _f in "${SCRIPT_DIR}"/*.sh; do
  [[ -f "${_f}" ]] || continue
  grep -q $'\r' "${_f}" 2>/dev/null || continue
  sed -i 's/\r$//' "${_f}" 2>/dev/null || { tr -d '\r' < "${_f}" > "${_f}.lf" && mv "${_f}.lf" "${_f}"; }
done

# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"

AI_CAR_ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS
DESKTOP="${UI_DESKTOP_DIR:-${HOME}/Desktop}"
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"

echo "==> 安装大模型控车到 ${SCRIPT_DIR}"
echo "    ROS 工作区: ${AI_CAR_ROS_WS}"

chmod +x "${SCRIPT_DIR}/ai_car_common.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/ai_car_"*.sh 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/ai_car_sudo_askpass.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/voice_to_llm.py" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/start_ai_car.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/run_ai_car_all.sh" 2>/dev/null || true

ai_car_copy_car_cmd "${RKSDK}" "${AI_CAR_ROS_WS}"

LAUNCHER="${DESKTOP}/NovaJoy-大模型控车.sh"
{
  echo '#!/usr/bin/env bash'
  echo 'export DISPLAY="${DISPLAY:-:0}"'
  echo "export CAR_CMD=\"${AI_CAR_ROS_WS}/car_cmd.sh\""
  echo 'export CAR_CMD_PYTHON="/usr/bin/python3"'
  echo "exec bash \"${SCRIPT_DIR}/run_ai_car_all.sh\""
} > "${LAUNCHER}"
chmod +x "${LAUNCHER}" 2>/dev/null || true

echo ""
echo "安装完成。"
echo "  一键总启动: bash ${SCRIPT_DIR}/run_ai_car_all.sh"
echo "  (终端1=语音 2=底盘 3=MicroROS 4=flask)"
echo "  或双击:   ${DESKTOP}/NovaJoy-大模型控车.sh"
