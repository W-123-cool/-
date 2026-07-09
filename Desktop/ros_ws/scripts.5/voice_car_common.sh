#!/usr/bin/env bash
# 语音控车共用函数（被 install / start 脚本 source）

voice_car_resolve_ros_ws() {
  local script_dir="${1:-}"
  local candidate

  if [[ -n "${VOICE_CAR_ROS_WS:-}" && -f "${VOICE_CAR_ROS_WS}/car_cmd.sh" ]]; then
    echo "${VOICE_CAR_ROS_WS}"
    return 0
  fi

  for candidate in \
    "${HOME}/Desktop/rock_ws/ros_ws" \
    "${script_dir}/.." \
    "${HOME}/rock_ws/ros_ws"; do
    if [[ -n "${candidate}" && -f "${candidate}/car_cmd.sh" ]]; then
      echo "$(cd "${candidate}" && pwd)"
      return 0
    fi
  done

  echo "${HOME}/Desktop/rock_ws/ros_ws"
}

voice_car_detect_astra_card() {
  arecord -l 2>/dev/null | grep -iE 'astra|orbbec' | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1
}

voice_car_detect_pulse_source() {
  command -v pactl >/dev/null 2>&1 || return 1
  pactl list sources short 2>/dev/null | awk '/[Oo]rbbec|[Aa]stra/ && !/monitor/ {print $2; exit}'
}

voice_car_detect_astra_device_or_die() {
  local card pulse_src

  card="$(voice_car_detect_astra_card)"
  if [[ -n "${card}" ]]; then
    echo "alsa:plughw:${card},0"
    return 0
  fi

  pulse_src="$(voice_car_detect_pulse_source)"
  if [[ -n "${pulse_src}" ]]; then
    echo "pulse:${pulse_src}"
    return 0
  fi

  echo "[错误] 未找到 Astra 麦克风。请确认 lsusb 有 2bc5:0403 后重试。" >&2
  lsusb | grep -i 2bc5 || true
  arecord -l || true
  pactl list sources short 2>/dev/null | grep -i orbbec || true
  exit 1
}

# 兼容旧调用
voice_car_detect_astra_card_or_die() {
  voice_car_detect_astra_device_or_die
}

voice_car_run_stt() {
  local device="$1"
  local py_script="$2"

  case "${device}" in
    alsa:*)
      exec arecord -D "${device#alsa:}" -f S16_LE -r 16000 -c 1 -t raw | python3 "${py_script}"
      ;;
    pulse:*)
      if ! command -v parecord >/dev/null 2>&1; then
        echo "[错误] 需要 parecord (pulseaudio-utils)" >&2
        exit 1
      fi
      exec parecord --device="${device#pulse:}" --format=s16le --rate=16000 --channels=1 --raw | \
        python3 "${py_script}"
      ;;
    *)
      echo "[错误] 未知录音设备: ${device}" >&2
      exit 1
      ;;
  esac
}

voice_car_open_terminal() {
  local title="$1"
  local inner_cmd="$2"
  export DISPLAY="${DISPLAY:-:0}"

  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title="${title}" -- bash -lc "${inner_cmd}; exec bash"
    return 0
  fi
  if command -v xfce4-terminal >/dev/null 2>&1; then
    xfce4-terminal --title="${title}" -e "bash -lc '${inner_cmd}; exec bash'"
    return 0
  fi
  if command -v lxterminal >/dev/null 2>&1; then
    lxterminal --title="${title}" -e "bash -lc '${inner_cmd}; exec bash'"
    return 0
  fi
  if command -v xterm >/dev/null 2>&1; then
    xterm -title "${title}" -e bash -lc "${inner_cmd}; exec bash" &
    return 0
  fi

  echo ""
  echo "[提示] 未能自动开新终端，请手动新开终端执行："
  echo "  ${inner_cmd}"
  echo ""
  return 1
}

voice_car_microros_running() {
  pgrep -f "micro_ros_agent.*8888" >/dev/null 2>&1
}

voice_car_prepare_serial() {
  local ros_ws="$1"
  local serial="${VOICE_CAR_SERIAL:-/dev/rt_shell}"
  local setup="${ros_ws}/usb_auto_setup.sh"

  if [[ -e "${serial}" ]]; then
    echo "  串口已就绪: ${serial}"
    return 0
  fi

  echo "  未找到 ${serial}，正在运行 usb_auto_setup.sh …"
  if [[ ! -f "${setup}" ]]; then
    echo "[警告] 未找到 ${setup}" >&2
    return 1
  fi

  ( cd "${ros_ws}" && bash "${setup}" ) || return 1

  if [[ -e "${serial}" ]]; then
    echo "  串口已创建: ${serial}"
    return 0
  fi

  echo "[警告] usb_auto_setup 完成但仍无 ${serial}" >&2
  return 1
}

voice_car_start_chassis_stack() {
  local script_dir="$1"
  local microros_ws="$2"
  local ros_ws="$3"
  local rock_ip

  rock_ip="$(hostname -I | awk '{print $1}')"
  export VOICE_CAR_MICROROS_WS="${microros_ws}"
  export VOICE_CAR_ROS_WS="${ros_ws}"
  export VOICE_CAR_ROCK_IP="${rock_ip}"

  echo ""
  echo "==== 底盘启动（自动开终端）===="

  voice_car_prepare_serial "${ros_ws}" || true

  echo ""
  echo "[终端1] MicroROS Agent"
  echo "  cd ${microros_ws}"
  echo "  source install/setup.bash"
  echo "  ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888"
  if voice_car_microros_running; then
    echo "  → MicroROS 已在运行，跳过"
  else
    voice_car_open_terminal "终端1-MicroROS" \
      "export VOICE_CAR_MICROROS_WS='${microros_ws}'; bash '${script_dir}/voice_car_microros_term.sh'"
    echo "  → 已新开终端，等待 Agent 启动 (3s)…"
    sleep 3
  fi

  echo ""
  echo "[终端2] 底盘 minicom"
  echo "  # 若 /dev/rt_shell 不存在，先运行 usb_auto_setup.sh"
  echo "  minicom -D /dev/rt_shell -b 1500000"
  echo "  RT-Thread shell 内输入："
  echo "    microros_chassis udp ${rock_ip} 8888"
  echo "    chassis_car_app"
  voice_car_open_terminal "终端2-底盘minicom" \
    "export VOICE_CAR_ROS_WS='${ros_ws}'; export VOICE_CAR_ROCK_IP='${rock_ip}'; bash '${script_dir}/voice_car_chassis_term.sh'"

  echo ""
  echo "----------------------------------------"
  echo "  请在「终端2-底盘minicom」完成 RT-Thread 命令后"
  echo "  回到本窗口继续"
  echo "----------------------------------------"
}

voice_car_source_ros() {
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/foxy/setup.bash
  if [[ -f "${VOICE_CAR_ROS_WS}/install/setup.bash" ]]; then
    # shellcheck disable=SC1091
    source "${VOICE_CAR_ROS_WS}/install/setup.bash"
  fi
  set -u
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
}
