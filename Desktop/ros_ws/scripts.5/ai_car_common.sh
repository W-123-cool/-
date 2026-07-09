#!/usr/bin/env bash
# 大模型控车 + 语音 — 共用函数
#
# Python 分工：
#   - ROS / Nav2 / initialpose → AI_CAR_ROS_PYTHON（默认 /usr/bin/python3 + apt python3-numpy）
#   - Sherpa STT/TTS / 语音 agent → rk3588-offline-bundle/venv（仅语音终端 activate）

export AI_CAR_ROS_PYTHON="${AI_CAR_ROS_PYTHON:-/usr/bin/python3}"
export AI_CAR_SYSTEM_PYTHON="${AI_CAR_SYSTEM_PYTHON:-/usr/bin/python3}"
ai_car_resolve_ros_ws() {
  local script_dir="${1:-}"
  local candidate
  if [[ -n "${AI_CAR_ROS_WS:-}" && -f "${AI_CAR_ROS_WS}/car_cmd.sh" ]]; then
    echo "${AI_CAR_ROS_WS}"
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

ai_car_open_terminal() {
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
  echo "[提示] 未能自动开新终端，请手动执行："
  echo "  ${inner_cmd}"
  echo ""
  return 1
}

ai_car_microros_running() {
  pgrep -f "micro_ros_agent.*8888" >/dev/null 2>&1
}

# 仅停导航/传感器；故意不杀 micro_ros_agent（避免 MCU 底盘 microros 会话断开）
ai_car_cleanup_nav_stack() {
  local _y='\033[1;33m' _c='\033[0;36m' _nc='\033[0m'
  echo -e "\n${_y}[退出] 正在停止导航与传感器进程…${_nc}"
  pkill -f "rt_robot_nav2_complete.launch.py" 2>/dev/null || true
  pkill -f "smart_switcher" 2>/dev/null || true
  pkill -f "smart_building_navigator" 2>/dev/null || true
  pkill -f "nav2_" 2>/dev/null || true
  pkill -f "amcl" 2>/dev/null || true
  pkill -f "map_server" 2>/dev/null || true
  pkill -f "auto_initialpose" 2>/dev/null || true
  echo -e "${_c}[保留] MicroROS Agent 仍在运行（避免 MCU 底盘会话断开）${_nc}"
  echo -e "${_c}       若需停止 Agent，请在「终端3-MicroROS」单独 Ctrl+C${_nc}"
}

ai_car_flask_running() {
  ai_car_llm_port_open
}

ai_car_llm_port_open() {
  local host
  host="$(ai_car_strip_env "${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}")"
  local code
  code="$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' "${host}/" 2>/dev/null || echo "000")"
  [[ "${code}" != "000" && -n "${code}" ]]
}

ai_car_strip_env() {
  local v="${1:-}"
  v="${v//$'\r'/}"
  v="${v//$'\n'/}"
  printf '%s' "${v}"
}

ai_car_normalize_voice_nav_env() {
  export DASHSCOPE_API_KEY="$(ai_car_strip_env "${DASHSCOPE_API_KEY:-}")"
  export DASHSCOPE_BASE_URL="$(ai_car_strip_env "${DASHSCOPE_BASE_URL:-}")"
  export DASHSCOPE_MODEL="$(ai_car_strip_env "${DASHSCOPE_MODEL:-}")"
  export VOICE_NAV_BACKEND="$(ai_car_strip_env "${VOICE_NAV_BACKEND:-auto}")"
  export VOICE_NAV_USE_LLM="$(ai_car_strip_env "${VOICE_NAV_USE_LLM:-1}")"
  export AI_CAR_LLM_HOST="$(ai_car_strip_env "${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}")"
  export AI_CAR_LLM_PATH="$(ai_car_strip_env "${AI_CAR_LLM_PATH:-/rkllm_chat}")"
}

ai_car_skip_local_llm() {
  [[ "${AI_CAR_SKIP_LOCAL_LLM:-0}" == "1" ]] && return 0
  [[ "${VOICE_NAV_SKIP_LOCAL_LLM:-0}" == "1" ]] && return 0
  return 1
}

ai_car_voice_nav_startup_probe() {
  local ros_ws="$1"
  python3 - <<PY
import os, sys
sys.path.insert(0, "${ros_ws}")
from voice_nav.startup_check import apply_startup, announce_startup, check_startup
r = check_startup(
    host=os.environ.get("AI_CAR_LLM_HOST", "http://127.0.0.1:8001"),
    path=os.environ.get("AI_CAR_LLM_PATH", "/rkllm_chat"),
    force=True,
)
apply_startup(r)
announce_startup(
    r,
    speak=r.ok,
    print_json=os.environ.get("VOICE_NAV_STARTUP_JSON", "0").strip().lower() in ("1", "true", "yes"),
)
sys.exit(0 if r.ok else 1)
PY
}

ai_car_wait_llm() {
  local max="${AI_CAR_LLM_WAIT_SEC:-180}"
  local waited=0
  if ai_car_llm_port_open; then
    echo "  大模型服务已就绪 (${AI_CAR_LLM_HOST:-http://127.0.0.1:8001})"
    return 0
  fi
  echo "  等待大模型服务启动 (最多 ${max}s)…"
  while ! ai_car_llm_port_open; do
    sleep 2
    waited=$((waited + 2))
    if (( waited >= max )); then
      echo "[错误] 大模型未就绪 (Connection refused)。请确认终端4 flask_server 已启动。" >&2
      return 1
    fi
    if (( waited % 10 == 0 )); then
      echo "  … 已等待 ${waited}s"
    fi
  done
  echo "  大模型服务已就绪"
  return 0
}

ai_car_usb_auto_setup() {
  local ros_ws="$1"
  local script_dir="${2:-}"
  local setup="${ros_ws}/usb_auto_setup.sh"
  local pass="${AI_CAR_SUDO_PASS:-rock}"
  local askpass="${script_dir}/ai_car_sudo_askpass.sh"

  if [[ ! -f "${setup}" ]]; then
    echo "[警告] 未找到 ${setup}" >&2
    return 1
  fi

  echo "  运行 usb_auto_setup.sh (sudo 密码: 已自动)…"
  (
    cd "${ros_ws}"
    if [[ -n "${pass}" ]]; then
      echo "${pass}" | sudo -S -v 2>/dev/null || true
    fi
    if [[ -f "${askpass}" ]]; then
      chmod +x "${askpass}" 2>/dev/null || true
      export SUDO_ASKPASS="${askpass}"
      export AI_CAR_SUDO_PASS="${pass}"
      sudo -A bash "${setup}" || bash "${setup}"
    else
      bash "${setup}"
    fi
  )
}

ai_car_prepare_serial() {
  local ros_ws="$1"
  local script_dir="${2:-}"
  local serial="${AI_CAR_SERIAL:-/dev/rt_shell}"
  if [[ -e "${serial}" ]]; then
    echo "  串口已就绪: ${serial}"
    return 0
  fi
  echo "  未找到 ${serial}，运行 usb_auto_setup.sh …"
  ai_car_usb_auto_setup "${ros_ws}" "${script_dir}" || return 1
  if [[ -e "${serial}" ]]; then
    echo "  串口已创建: ${serial}"
    return 0
  fi
  echo "[警告] usb_auto_setup 完成但仍无 ${serial}" >&2
  return 1
}

ai_car_send_chassis_cmds() {
  local rock_ip="$1"
  local serial="${AI_CAR_SERIAL:-/dev/rt_shell}"
  local baud="${AI_CAR_BAUD:-1500000}"

  if [[ ! -e "${serial}" ]]; then
    return 1
  fi

  echo "  尝试自动发送底盘命令到 ${serial} …"
  stty -F "${serial}" "${baud}" raw -echo min 0 time 0 2>/dev/null || true
  {
    sleep 0.8
    printf 'microros_chassis udp %s 8888\r\n' "${rock_ip}"
    sleep 3
    printf 'chassis_car_app\r\n'
  } > "${serial}" 2>/dev/null || return 1
  sleep 4
  echo "  已发送: microros_chassis udp ${rock_ip} 8888"
  echo "          chassis_car_app"
  echo "  若底盘未响应，请在终端2 minicom 中手动输入上述命令"
  return 0
}

ai_car_copy_car_cmd() {
  local rksdk="$1"
  local ros_ws="$2"
  local copied=0
  local f
  for f in car_cmd.sh car_cmd_daemon.py; do
    if [[ -f "${rksdk}/${f}" ]]; then
      cp -f "${rksdk}/${f}" "${ros_ws}/${f}"
      chmod +x "${ros_ws}/${f}" 2>/dev/null || true
      echo "  已复制 ${f} -> ${ros_ws}/"
      copied=1
    fi
  done
  if [[ "${copied}" -eq 0 ]]; then
    echo "[警告] 未在 ${rksdk} 找到 car_cmd.sh / car_cmd_daemon.py" >&2
    echo "  若 ros_ws 里已有 car_cmd.sh 可继续" >&2
  fi
}

ai_car_detect_astra_device_or_die() {
  local card pulse_src
  if [[ -n "${AI_CAR_AUDIO_DEV:-}" ]]; then
    echo "${AI_CAR_AUDIO_DEV}"
    return 0
  fi
  card="$(arecord -l 2>/dev/null | grep -iE 'astra|orbbec' | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1)"
  if [[ -n "${card}" ]]; then
    echo "alsa:plughw:${card},0"
    return 0
  fi
  if command -v pactl >/dev/null 2>&1; then
    pulse_src="$(pactl list sources short 2>/dev/null | awk '/[Oo]rbbec|[Aa]stra/ && !/monitor/ {print $2; exit}')"
    if [[ -n "${pulse_src}" ]]; then
      echo "pulse:${pulse_src}"
      return 0
    fi
  fi
  echo "[错误] 未找到 Astra 麦克风 (lsusb 需有 2bc5:0403)" >&2
  echo "  可手动指定: export AI_CAR_AUDIO_DEV=alsa:plughw:4,0  （arecord -l 查 card 号）" >&2
  lsusb | grep -i 2bc5 || true
  arecord -l || true
  exit 1
}

ai_car_run_stt() {
  local device="$1"
  local py_script="$2"
  export AI_CAR_AUDIO_DEV="${device}"
  export VOICE_NAV_AGENT_SCRIPT="${py_script}"
  # 按需开麦：Python 内部启停 arecord，不再使用持续管道
  exec python3 "${py_script}"
}

ai_car_nice_prefix() {
  if nice -n -5 true 2>/dev/null; then
    printf '%s' "nice -n -5 "
  fi
}

# nav_action_bridge 写入就绪标记（比 ros2 action info 可靠）
ai_car_nav_bridge_ready_file() {
  local p=""
  for p in \
    "${SMART_NAV_BRIDGE_READY_FILE:-}" \
    "/tmp/smart_nav_bridge.ready" \
    "${AI_CAR_ROS_WS:-}/.nav_bridge_ready" \
    "${HOME}/.nav_bridge_ready"; do
    [[ -n "${p}" && -f "${p}" ]] && return 0
  done
  return 1
}

ai_car_wait_nav_bridge_ready() {
  local max_sec="${1:-${VOICE_NAV_BRIDGE_WAIT_SEC:-25}}"
  local i=0
  echo "  [nav-bridge] 等待 nav_action_bridge 连接（最多 ${max_sec}s）…"
  while (( i < max_sec )); do
    if pgrep -f "smart_switcher" >/dev/null 2>&1 && ai_car_nav_bridge_ready_file; then
      echo "  [nav-bridge] OK (+${i}s)"
      return 0
    fi
    if pgrep -f "smart_switcher" >/dev/null 2>&1 && ai_car_nav_action_ready; then
      echo "  [nav-bridge] OK (+${i}s): action server via ros2 action info"
      return 0
    fi
    if (( i > 0 && i % 5 == 0 )); then
      echo "  [nav-bridge] ${i}/${max_sec}s …"
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "  [nav-bridge] 警告: 未检测到 bridge 就绪，仍将继续" >&2
  return 1
}

# Nav2 稳定后再启语音：switcher + bridge 就绪文件或 action
ai_car_wait_nav_stack_ready() {
  local max_sec="${1:-${VOICE_NAV_NAV_READY_TIMEOUT:-25}}"
  local i=0
  echo "  [nav-ready] 等待 switcher + nav_action_bridge（约 ${max_sec}s）…"
  while (( i < max_sec )); do
    if pgrep -f "smart_switcher" >/dev/null 2>&1 \
      && { ai_car_nav_bridge_ready_file || ai_car_nav_action_ready; }; then
      echo "  [nav-ready] OK (+${i}s): 导航栈可接受 MQTT/语音导航"
      return 0
    fi
    if (( i > 0 && i % 5 == 0 )); then
      local sw=0 br=0
      pgrep -f "smart_switcher" >/dev/null 2>&1 && sw=1
      ai_car_nav_bridge_ready_file && br=1
      echo "  [nav-ready] ${i}/${max_sec}s (switcher=${sw}, bridge_file=${br})"
    fi
    sleep 1
    i=$((i + 1))
  done
  if pgrep -f "smart_switcher" >/dev/null 2>&1; then
    echo "  [nav-ready] OK (+${max_sec}s): switcher 已运行，放行启动语音"
    return 0
  fi
  echo "  [nav-ready] 警告: switcher 未检测到，仍将尝试启动语音" >&2
  return 1
}

ai_car_source_ros() {
  local ws="${AI_CAR_ROS_WS:-}"
  if [[ -z "${ws}" && -n "${ROS_WS:-}" ]]; then
    ws="${ROS_WS}"
  fi
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/foxy/setup.bash
  if [[ -n "${ws}" && -f "${ws}/install/setup.bash" ]]; then
    # shellcheck disable=SC1091
    source "${ws}/install/setup.bash"
  fi
  set -u
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
}

# ROS / Nav2：强制系统 Python，不继承 Sherpa venv（避免 geometry_msgs 找不到 numpy）
ai_car_prepare_ros_cli() {
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    deactivate 2>/dev/null || true
  fi
  unset VIRTUAL_ENV PYTHONHOME

  local _path_clean="" _pp_clean="" _p _part
  IFS=':' read -ra _parts <<< "${PATH:-}"
  for _part in "${_parts[@]}"; do
    [[ "${_part}" == *"/venv/"* || "${_part}" == *"/.venv/"* ]] && continue
    [[ -z "${_path_clean}" ]] && _path_clean="${_part}" || _path_clean="${_path_clean}:${_part}"
  done
  export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${_path_clean}"

  if [[ -n "${PYTHONPATH:-}" ]]; then
    IFS=':' read -ra _parts <<< "${PYTHONPATH}"
    for _part in "${_parts[@]}"; do
      [[ "${_part}" == *"/venv/"* || "${_part}" == *"/.venv/"* ]] && continue
      [[ -z "${_pp_clean}" ]] && _pp_clean="${_part}" || _pp_clean="${_pp_clean}:${_part}"
    done
    export PYTHONPATH="${_pp_clean}"
  fi

  ai_car_source_ros
}

ai_car_verify_system_numpy() {
  if "${AI_CAR_ROS_PYTHON}" -c "import numpy; from geometry_msgs.msg import PoseWithCovarianceStamped" 2>/dev/null; then
    return 0
  fi
  echo "  [ros-python] 系统 Python 缺少 numpy/geometry_msgs，请执行:" >&2
  echo "  sudo apt install -y python3-numpy" >&2
  echo "  验证: ${AI_CAR_ROS_PYTHON} -c \"import numpy; from geometry_msgs.msg import PoseWithCovarianceStamped\"" >&2
  return 1
}

ai_car_nav_terminal_preamble() {
  printf "unset VIRTUAL_ENV PYTHONHOME; export AI_CAR_ROS_PYTHON='%s'; export AI_CAR_SCRIPT_DIR='%s'; " \
    "${AI_CAR_ROS_PYTHON}" "${1:-}"
}

ai_car_sensors_running() {
  pgrep -f "dm_imu_rviz.launch.py" >/dev/null 2>&1 \
    || pgrep -f "dm_imu_node" >/dev/null 2>&1
}

ai_car_lidar_running() {
  pgrep -f "lslidar_driver_node" >/dev/null 2>&1
}

ai_car_read_map_initial_pose() {
  local yaml="$1"
  local _x _y _yaw
  if [[ ! -f "${yaml}" ]]; then
    return 1
  fi
  if grep -q 'rt_robot_initial_pose:' "${yaml}" 2>/dev/null; then
    _x="$(grep -A6 'rt_robot_initial_pose:' "${yaml}" | grep 'x:' | head -1 | awk '{print $2}')"
    _y="$(grep -A6 'rt_robot_initial_pose:' "${yaml}" | grep 'y:' | head -1 | awk '{print $2}')"
    _yaw="$(grep -A6 'rt_robot_initial_pose:' "${yaml}" | grep 'yaw:' | head -1 | awk '{print $2}')"
  elif grep -qE '^initial_pose:' "${yaml}" 2>/dev/null; then
    _x="$(grep -A6 '^initial_pose:' "${yaml}" | grep 'x:' | head -1 | awk '{print $2}')"
    _y="$(grep -A6 '^initial_pose:' "${yaml}" | grep 'y:' | head -1 | awk '{print $2}')"
    _yaw="$(grep -A6 '^initial_pose:' "${yaml}" | grep 'yaw:' | head -1 | awk '{print $2}')"
  else
    return 1
  fi
  [[ -n "${_x}" && -n "${_y}" && -n "${_yaw}" ]] || return 1
  printf '%s %s %s' "${_x}" "${_y}" "${_yaw}"
}

# Publish /initialpose via ros2 CLI (no numpy; uses system python math only)
ai_car_warn_numpy_for_auto_initialpose() {
  if ai_car_verify_system_numpy; then
    return 0
  fi
  echo "  [initialpose] launch 内 auto_initialpose 需要系统 python3-numpy（不用 venv）" >&2
  return 1
}

ai_car_wait_amcl_active() {
  local max_sec="${1:-45}"
  local i=0
  ai_car_prepare_ros_cli
  while (( i < max_sec )); do
    if ai_car_amcl_localized; then
      echo "  [initialpose] AMCL 已定位 (+${i}s)"
      return 0
    fi
    local state=""
    state="$(ros2 lifecycle get /amcl 2>/dev/null | tail -1 || ros2 lifecycle get amcl 2>/dev/null | tail -1 || true)"
    if [[ "${state}" == *"active"* ]]; then
      echo "  [initialpose] AMCL lifecycle=active (+${i}s)"
      sleep 1
      if ai_car_amcl_localized; then
        return 0
      fi
    fi
    if (( i > 0 && i % 5 == 0 )); then
      echo "  [initialpose] 等待 AMCL 定位… ${i}/${max_sec}s"
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "  [initialpose] 警告: ${max_sec}s 内未确认定位，仍尝试发布" >&2
  return 1
}

ai_car_amcl_localized() {
  if [[ -z "${_AI_CAR_ROS_CLI_READY:-}" ]]; then
    ai_car_prepare_ros_cli
    _AI_CAR_ROS_CLI_READY=1
    export _AI_CAR_ROS_CLI_READY
  fi
  if timeout 6 ros2 topic echo /amcl_pose geometry_msgs/msg/PoseWithCovarianceStamped --once 2>/dev/null \
    | grep -q "position:"; then
    return 0
  fi
  if timeout 3 ros2 run tf2_ros tf2_echo map odom 2>/dev/null | head -12 | grep -q "Translation:"; then
    return 0
  fi
  return 1
}

# 6x6 协方差展平（必须正好 36 个数）
ai_car_initialpose_covariance_yaml() {
  echo "[0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.06853891909122467]"
}

ai_car_ros2_pub_initialpose_once() {
  local x="$1" y="$2" qz="$3" qw="$4"
  local script_dir="${AI_CAR_SCRIPT_DIR:-}"
  if [[ -z "${script_dir}" ]]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  ai_car_prepare_ros_cli
  "${AI_CAR_ROS_PYTHON}" "${script_dir}/publish_initial_pose_once.py" "${x}" "${y}" "${qz}" "${qw}"
}

ai_car_publish_initial_pose() {
  local map_yaml="$1"
  local pose x y yaw qz qw
  local async="${VOICE_NAV_INITIALPOSE_ASYNC:-0}"
  pose="$(ai_car_read_map_initial_pose "${map_yaml}" 2>/dev/null)" || pose=""
  if [[ -n "${pose}" ]]; then
    read -r x y yaw <<< "${pose}"
  else
    x="${VOICE_NAV_INIT_X:--0.254}"
    y="${VOICE_NAV_INIT_Y:-0.551}"
    yaw="${VOICE_NAV_INIT_YAW:-0.203}"
    echo "  [initialpose] yaml 无 initial_pose，使用默认 x=${x} y=${y} yaw=${yaw}"
  fi
  read -r qz qw <<< "$("${AI_CAR_ROS_PYTHON}" -c "import math; y=float('${yaw}'); print(math.sin(y/2), math.cos(y/2))")"
  echo "  [initialpose] x=${x} y=${y} yaw=${yaw} -> /initialpose (python pub)"
  ai_car_warn_numpy_for_auto_initialpose || true

  _ai_car_publish_initial_pose_loop() {
    local _x="$1" _y="$2" _qz="$3" _qw="$4"
    local max_attempts="${VOICE_NAV_INITIALPOSE_RETRIES:-10}"
    local verify_only="${VOICE_NAV_INITIALPOSE_VERIFY_ONLY:-1}"
    local i=0
    unset _AI_CAR_ROS_CLI_READY
    ai_car_prepare_ros_cli
    ai_car_wait_amcl_active "${VOICE_NAV_INITIALPOSE_WAIT_SEC:-30}" || true
    if ai_car_amcl_localized; then
      echo "  [initialpose] 定位已就绪 (auto_initialpose / AMCL)"
      return 0
    fi
    if [[ "${verify_only}" == "1" ]]; then
      echo "  [initialpose] 仅验证模式：auto_initialpose 未确认，尝试补发一次" >&2
      max_attempts=3
    fi
    while (( i < max_attempts )); do
      if ai_car_amcl_localized; then
        echo "  [initialpose] 定位已就绪 (amcl_pose / map->odom)"
        return 0
      fi
      if ai_car_ros2_pub_initialpose_once "${_x}" "${_y}" "${_qz}" "${_qw}"; then
        :
      else
        echo "  [initialpose] python pub 失败 (第 $((i + 1)) 次)" >&2
      fi
      sleep 2
      i=$((i + 1))
    done
    echo "  [initialpose] 超时：请 RViz 2D Pose Estimate 校准位置" >&2
    return 1
  }

  if [[ "${async}" == "1" ]]; then
    _ai_car_publish_initial_pose_loop "${x}" "${y}" "${qz}" "${qw}" &
  else
    _ai_car_publish_initial_pose_loop "${x}" "${y}" "${qz}" "${qw}"
  fi
}

ai_car_usb_setup_slam_style() {
  local ros_ws="$1"
  local pass="${AI_CAR_SUDO_PASS:-rock}"
  local setup="${ros_ws}/usb_auto_setup.sh"
  if [[ ! -f "${setup}" ]]; then
    return 0
  fi
  echo "  配置 USB 权限…"
  echo "${pass}" | sudo -S bash "${setup}" >/dev/null 2>&1 || true
  echo "${pass}" | sudo -S udevadm control --reload-rules >/dev/null 2>&1 || true
  echo "${pass}" | sudo -S udevadm trigger >/dev/null 2>&1 || true
}

ai_car_start_sensors_slam_style() {
  local with_rviz="${1:-1}"
  if ai_car_sensors_running; then
    echo "  传感器已在运行"
    return 0
  fi
  echo "  启动 IMU + 雷达…"
  if [[ "${with_rviz}" == "1" ]]; then
    ros2 launch dm_imu dm_imu_rviz.launch.py &
  else
    ros2 launch dm_imu dm_imu.launch.py &
  fi
  sleep 1
  ros2 launch lslidar_driver lsn10p_launch.py &
  sleep 2
}

# 停掉预热用的传感器 launch（Nav2 bringup 会独占 /dev/laser、/dev/imu）
ai_car_stop_prewarm_sensor_launches() {
  if ! ai_car_sensors_running && ! ai_car_lidar_running; then
    return 0
  fi
  echo "  停止预热传感器进程，交给 Nav2 bringup 独占设备…"
  pkill -f "dm_imu_rviz.launch.py" 2>/dev/null || true
  pkill -f "dm_imu.launch.py" 2>/dev/null || true
  pkill -f "lsn10p_launch.py" 2>/dev/null || true
  pkill -f "dm_imu_node" 2>/dev/null || true
  pkill -f "lslidar_driver_node" 2>/dev/null || true
  sleep 2
}

ai_car_start_microros_slam_style() {
  local microros_ws="$1"
  if ai_car_microros_running; then
    echo "  MicroROS 已在运行"
    return 0
  fi
  if [[ "${AI_CAR_MICROROS_IN_TERMINAL:-0}" == "1" ]]; then
    return 1
  fi
  echo "  启动 MicroROS Agent (UDP 8888, 后台)…"
  (
    cd "${microros_ws}"
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/foxy/setup.bash
    # shellcheck disable=SC1091
    source install/setup.bash
    set -u
    export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
    exec ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
  ) &
  local agent_pid=$!
  disown "${agent_pid}" 2>/dev/null || true
  echo "  Agent PID ${agent_pid}（已 disown，其他终端 Ctrl+C 不会停止 Agent）"
  sleep 2
}

ai_car_wait_chassis_slam_style() {
  local rock_ip="$1"
  local serial="${AI_CAR_SERIAL:-/dev/rt_shell}"
  local baud="${AI_CAR_BAUD:-1500000}"
  echo ""
  echo "=================================================="
  echo "  【重要】请在终端2连接底盘 (minicom):"
  echo "  minicom -D ${serial} -b ${baud}"
  echo "  microros_chassis udp ${rock_ip} 8888"
  echo "  chassis_car_app"
  echo "  看到 ROS CAR START SUCCESSFULLY 后继续"
  echo "=================================================="
  if [[ "${AI_CAR_SKIP_CHASSIS_PROMPT:-0}" == "1" ]]; then
    echo "  [注意] AI_CAR_SKIP_CHASSIS_PROMPT=1 — 未等待底盘确认"
    return 0
  fi
  read -r -p "按 Enter 键确认底盘已就绪…"
}

ai_car_prewarm_sensors_multi_map_style() {
  if [[ "${VOICE_NAV_SKIP_SENSOR_PREWARM:-0}" == "1" ]]; then
    echo "  跳过传感器预热 (VOICE_NAV_SKIP_SENSOR_PREWARM=1)"
    return 0
  fi
  if ai_car_sensors_running && ai_car_lidar_running; then
    echo "  IMU/雷达已在运行，跳过预热"
    return 0
  fi
  echo "  预热 IMU + 雷达 (无 RViz，仅唤醒 USB 设备)…"
  ai_car_start_sensors_slam_style 0
}

# 快速 lifecycle 探测（CPU 高时 timeout 1s 易误报，默认 3s）
ai_car_ros_lifecycle_active() {
  local node="$1"
  local timeout_sec="${2:-3}"
  local state=""
  state="$(timeout "${timeout_sec}" ros2 lifecycle get "${node}" 2>/dev/null | tail -1 || true)"
  [[ "${state}" == *"active"* ]]
}

# /navigate_to_pose action 是否已有 bt_navigator 提供服务
ai_car_nav_action_ready() {
  local out=""
  out="$(timeout 5 ros2 action info /navigate_to_pose 2>/dev/null || true)"
  [[ -n "${out}" ]] && echo "${out}" | grep -qi bt_navigator
}

# 方案 A：Nav2 launch 后固定短延迟，不再 90s lifecycle 轮询
ai_car_wait_nav2_boot() {
  local fixed_sec="${1:-${VOICE_NAV_NAV_FIXED_SEC:-10}}"
  echo "  [nav2] 固定等待 Nav2 启动 ${fixed_sec}s …"
  sleep "${fixed_sec}"
}

ai_car_voice_nav_stack_env() {
  printf "export VOICE_NAV_SKIP_BUILD=1 VOICE_NAV_SKIP_USB=1 VOICE_NAV_SKIP_SENSORS=1; "
  printf "export VOICE_NAV_USE_DEPTH_NAV='%s'; " "${VOICE_NAV_USE_DEPTH_NAV:-0}"
  printf "export VOICE_NAV_AUTO_INITIALPOSE='%s'; " "${VOICE_NAV_AUTO_INITIALPOSE:-0}"
  printf "export VOICE_NAV_OPEN_RVIZ='%s'; " "${VOICE_NAV_OPEN_RVIZ:-false}"
  printf "export SMART_NAV_ACTION_WAIT_SEC='%s'; " "${SMART_NAV_ACTION_WAIT_SEC:-30}"
  printf "export SMART_NAV_BRIDGE_READY_FILE='%s'; " "${SMART_NAV_BRIDGE_READY_FILE:-/tmp/smart_nav_bridge.ready}"
}

# 按 slam_mapping.sh / start_multi_map 时序：USB → 传感器预热 → MicroROS → 底盘 → flask
ai_car_start_stack() {
  local script_dir="$1"
  local microros_ws="$2"
  local ros_ws="$3"
  local rksdk="$4"
  local llm_dir="$5"
  local rock_ip

  rock_ip="$(hostname -I | awk '{print $1}')"
  export AI_CAR_MICROROS_WS="${microros_ws}"
  export AI_CAR_ROS_WS="${ros_ws}"
  export AI_CAR_RKSDK="${rksdk}"
  export AI_CAR_LLM_DIR="${llm_dir}"
  export AI_CAR_ROCK_IP="${rock_ip}"
  export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"

  echo ""
  echo "==== [0] 复制 car_cmd 到 ros_ws ===="
  ai_car_copy_car_cmd "${rksdk}" "${ros_ws}"

  echo ""
  echo "==== [1] USB 权限 (同 slam_mapping) ===="
  ai_car_usb_setup_slam_style "${ros_ws}"

  echo ""
  echo "==== [2] MicroROS Agent (UDP 8888) ===="
  if ai_car_microros_running; then
    echo "  MicroROS 已在运行"
  elif [[ "${AI_CAR_MICROROS_IN_TERMINAL:-1}" == "1" ]]; then
    ai_car_open_terminal "终端3-MicroROS" \
      "export AI_CAR_MICROROS_WS='${microros_ws}'; bash '${script_dir}/ai_car_microros_term.sh'"
    sleep 2
  else
    ai_car_start_microros_slam_style "${microros_ws}" || true
  fi

  echo ""
  echo "==== [3] 终端2 — 底盘 minicom ===="
  ai_car_prepare_serial "${ros_ws}" "${script_dir}" || true
  ai_car_open_terminal "终端2-底盘minicom" \
    "export AI_CAR_ROS_WS='${ros_ws}' AI_CAR_ROCK_IP='${rock_ip}'; bash '${script_dir}/ai_car_chassis_term.sh'"

  ai_car_wait_chassis_slam_style "${rock_ip}"

  echo ""
  echo "==== [4] 终端4 — 大模型 flask (端口 8001) ===="
  if ai_car_skip_local_llm; then
    echo "  云端模式，跳过本地 flask (VOICE_NAV_SKIP_LOCAL_LLM=1)"
  elif ai_car_flask_running; then
    echo "  flask_server 已在运行"
  else
    ai_car_open_terminal "终端4-LLM服务" \
      "export AI_CAR_RKSDK='${rksdk}' AI_CAR_LLM_DIR='${llm_dir}'; bash '${script_dir}/ai_car_llm_server_term.sh'"
    if ! ai_car_wait_llm; then
      echo "[警告] 大模型启动超时，无网时将无法使用本地大模型" >&2
    fi
  fi

  if [[ "${AI_CAR_OPEN_AICHAT:-0}" == "1" ]]; then
    echo ""
    echo "==== [可选] 键盘对话 aichat ===="
    ai_car_open_terminal "aichat键盘" \
      "export AI_CAR_RKSDK='${rksdk}' AI_CAR_LLM_DIR='${llm_dir}'; bash '${script_dir}/ai_car_chat_term.sh'"
  fi

  echo ""
  echo "==== [5] 预热 IMU/雷达 (对齐 start_multi_map [3]) ===="
  ai_car_prewarm_sensors_multi_map_style

  echo ""
  echo "==== [6] 底盘已确认 — 下一步终端5 启动 Nav2 + smart_switcher ===="
  echo "  (同 start_multi_map: ros2 launch + smart_switcher + auto_initialpose)"
  echo "  语音 agent 将在 Nav2 稳定后再启动 (降低 CPU 争抢)"
  echo "  依赖: MicroROS + 底盘 + flask（无网时）/ 云端 API（有网时 auto）"
}
