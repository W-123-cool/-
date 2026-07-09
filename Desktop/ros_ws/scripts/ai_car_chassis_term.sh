#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"

ROCK_IP="${AI_CAR_ROCK_IP:-$(hostname -I | awk '{print $1}')}"
ROS_WS="${AI_CAR_ROS_WS:-$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")}"
SERIAL="${AI_CAR_SERIAL:-/dev/rt_shell}"
BAUD="${AI_CAR_BAUD:-1500000}"
export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"

clear
echo "=============================================="
echo "  终端2 — 底盘 (请手动操作)"
echo "=============================================="
echo "  ① 若无 /dev/rt_shell，脚本已尝试 usb_auto_setup"
echo "  ② 进入 minicom 后，在 msh /> 手动输入："
echo ""
echo "     microros_chassis udp ${ROCK_IP} 8888"
echo "     chassis_car_app"
echo ""
echo "  ③ 看到 ROS CAR START SUCCESSFULLY 即成功"
echo "  ④ 回到主窗口按 Enter 继续后续启动"
echo "=============================================="
echo ""

if [[ ! -e "${SERIAL}" ]]; then
  ai_car_usb_auto_setup "${ROS_WS}" "${SCRIPT_DIR}" || true
fi

if [[ ! -e "${SERIAL}" ]]; then
  echo "[错误] 串口不存在: ${SERIAL}"
  exec bash
fi

echo ">>> 启动 minicom，请在 msh /> 中手动输入上述两条命令"
echo ""
exec minicom -D "${SERIAL}" -b "${BAUD}"
