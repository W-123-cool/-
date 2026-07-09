#!/usr/bin/env bash
# 终端2 — 底盘 minicom（由 start_voice_car.sh 自动打开）
ROCK_IP="${VOICE_CAR_ROCK_IP:-$(hostname -I | awk '{print $1}')}"
ROS_WS="${VOICE_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}"
SERIAL="${VOICE_CAR_SERIAL:-/dev/rt_shell}"
BAUD="${VOICE_CAR_BAUD:-1500000}"
USB_SETUP="${ROS_WS}/usb_auto_setup.sh"

clear
echo "=============================================="
echo "  终端2 — 底盘串口 (minicom)"
echo "=============================================="
echo ""
echo "步骤："
echo "  1) 若 /dev/rt_shell 不存在，先运行 usb_auto_setup.sh"
echo "  2) 启动 minicom"
echo "  3) 进入 RT-Thread shell 后依次输入："
echo ""
echo "     microros_chassis udp ${ROCK_IP} 8888"
echo "     chassis_car_app"
echo ""
echo "  4) 完成后回到「语音控车」窗口按 Enter"
echo "=============================================="
echo ""

if [[ ! -e "${SERIAL}" ]]; then
  if [[ -f "${USB_SETUP}" ]]; then
    echo ">>> 正在执行: bash ${USB_SETUP}"
    ( cd "${ROS_WS}" && bash "${USB_SETUP}" ) || true
  else
    echo "[警告] 未找到 ${USB_SETUP}"
  fi
fi

if [[ ! -e "${SERIAL}" ]]; then
  echo "[错误] 串口仍不存在: ${SERIAL}"
  echo "请检查底盘 USB 连接后重试"
  exec bash
fi

echo ">>> 即将启动: minicom -D ${SERIAL} -b ${BAUD}"
echo ""
read -r -p "按 Enter 进入 minicom…" _
exec minicom -D "${SERIAL}" -b "${BAUD}"
