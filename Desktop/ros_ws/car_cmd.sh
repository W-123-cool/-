#!/bin/bash

set -euo pipefail

usage() {
  cat <<'EOF'
用法:
  bash car_cmd.sh [--direct] [--hold] <动作> [速度] [持续秒数]
  bash car_cmd.sh status

动作:
  fwd|forward     前进
  back|backward   后退
  left            左移
  right           右移
  ccw|left_turn   原地左转
  cw|right_turn   原地右转
  stop            停止
  shutdown        停止并关闭后台发布器
  warmup          预热（启动后台发布器，不让小车动）

参数:
  速度:
    - fwd/back/left/right: 线速度 m/s（默认 0.15）
    - ccw/cw: 角速度 rad/s（默认 0.30）
  持续秒数:
    - 不填：默认后台持续运动（命令会立刻返回），用 `bash car_cmd.sh stop` 停止
    - 如需前台持续运动：加 `--hold`（按 Ctrl+C 停）
    - 填了：运行指定秒数后自动停止

示例:
  bash car_cmd.sh fwd
  bash car_cmd.sh left
  bash car_cmd.sh ccw
  bash car_cmd.sh warmup
  bash car_cmd.sh fwd 0.2 1.5
  bash car_cmd.sh --direct fwd
  bash car_cmd.sh status

说明:
  - 为降低“命令下发到执行”的延迟，本脚本默认使用后台发布器（rclpy）持续发布 Twist。
  - 若需要回退到旧方式（每次调用都执行 `ros2 topic pub`），可设置环境变量：CAR_CMD_LEGACY=1
  - 若你在 python 虚拟环境（.venv）里运行导致后台发布器启动失败，可设置：CAR_CMD_PYTHON=/usr/bin/python3
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# NOTE: ROS2/colcon generated setup scripts are not guaranteed to be "nounset" safe.
# E.g. ros_ws/install/setup.bash may reference $COLCON_TRACE directly.
# Since this script uses `set -u`, temporarily disable nounset while sourcing ROS env.
with_nounset_off() {
  set +u
  "$@"
  set -u
}

# Auto source ROS env (prefer workspace install)
if [[ -f "$SCRIPT_DIR/install/setup.bash" ]]; then
  with_nounset_off source "$SCRIPT_DIR/install/setup.bash"
elif [[ -f "/opt/ros/foxy/setup.bash" ]]; then
  with_nounset_off source "/opt/ros/foxy/setup.bash"
fi

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ERROR: 找不到 ros2 命令。请先 source ROS 环境（例如 ros_ws/install/setup.bash）。"
  exit 1
fi

resolve_ros_python() {
  # Prefer explicit override (useful when user is inside a venv without rclpy).
  if [[ -n "${CAR_CMD_PYTHON:-}" ]]; then
    echo "${CAR_CMD_PYTHON}"
    return
  fi

  local ros2_bin shebang interp
  ros2_bin="$(command -v ros2 2>/dev/null || true)"
  if [[ -n "${ros2_bin}" && -r "${ros2_bin}" ]]; then
    shebang="$(head -n 1 "${ros2_bin}" 2>/dev/null || true)"
    if [[ "${shebang}" == \#!* ]]; then
      interp="${shebang#\#!}"
      interp="${interp%% *}"
      # If ros2 uses /usr/bin/env, it may resolve to a venv python; fallback to system python.
      if [[ "${interp}" != "/usr/bin/env" && -x "${interp}" ]]; then
        echo "${interp}"
        return
      fi
    fi
  fi

  if [[ -x "/usr/bin/python3" ]]; then
    echo "/usr/bin/python3"
    return
  fi
  echo "python3"
}

topic=""
qos_reliability="best_effort"
qos_depth="5"
force_direct=false
hold_mode=false
while [[ "${1:-}" == --* ]]; do
  case "${1}" in
    --direct)
      force_direct=true
      shift
      ;;
    --hold)
      hold_mode=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: 未知参数: ${1}"
      echo
      usage
      exit 2
      ;;
  esac
done

home_dir="${HOME:-/tmp}"
cache_base="${XDG_CACHE_HOME:-${home_dir}/.cache}"
state_dir="${cache_base}/car_cmd"
pid_file="${state_dir}/publisher.pid"
meta_file="${state_dir}/publisher.meta"
log_file="${state_dir}/publisher.log"
cmd_file="${state_dir}/cmd.json"
daemon_script="${SCRIPT_DIR}/car_cmd_daemon.py"
daemon_rate="${CAR_CMD_RATE:-20}"
daemon_idle_exit="${CAR_CMD_IDLE_EXIT:-30}"
ready_file="${state_dir}/daemon.ready"
daemon_python="$(resolve_ros_python)"
DAEMON_STARTED_NEW=0
startup_wait="${CAR_CMD_STARTUP_WAIT:-0.0}"

ensure_state_dir() {
  mkdir -p "$state_dir"
}

read_pid() {
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null | tr -d ' \t\r\n' || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  echo "$pid"
}

publisher_running() {
  local pid
  pid="$(read_pid)" || return 1
  kill -0 "$pid" 2>/dev/null
}

stop_publisher() {
  local pid
  pid="$(read_pid)" || { rm -f "$pid_file" "$meta_file" 2>/dev/null || true; return 0; }

  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 50); do
      if kill -0 "$pid" 2>/dev/null; then
        sleep 0.05
      else
        break
      fi
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi

  rm -f "$pid_file" "$meta_file" 2>/dev/null || true
}

meta_get() {
  local key="$1"
  [[ -f "$meta_file" ]] || return 1
  awk -F= -v k="$key" '$1==k{print $2; exit}' "$meta_file" 2>/dev/null | tr -d '\r' || true
}

daemon_signature() {
  [[ -f "$daemon_script" ]] || return 1
  cksum "$daemon_script" 2>/dev/null | awk '{print $1 ":" $2}' || true
}

write_cmd_json() {
  local vx="$1"
  local vy="$2"
  local wz="$3"
  ensure_state_dir
  local tmp
  tmp="$(mktemp "${state_dir}/cmd.XXXXXX")"
  printf '{"vx":%s,"vy":%s,"wz":%s,"ts":%s}\n' "$vx" "$vy" "$wz" "$(date +%s.%N)" >"$tmp"
  mv -f "$tmp" "$cmd_file"
}

want_direct_flag() {
  if $force_direct; then
    echo "1"
  else
    echo "0"
  fi
}

should_use_daemon() {
  [[ "${CAR_CMD_LEGACY:-0}" != "1" ]] || return 1
  [[ -f "$daemon_script" ]] || return 1
  if [[ "$daemon_python" == */* ]]; then
    [[ -x "$daemon_python" ]] || return 1
  else
    command -v "$daemon_python" >/dev/null 2>&1 || return 1
  fi
  return 0
}

wait_for_daemon_ready() {
  # Wait for daemon to finish initializing (publisher created) to avoid missing short timed moves.
  local tries="${1:-60}" # 60 * 0.05s = 3s
  for _ in $(seq 1 "$tries"); do
    [[ -f "$ready_file" ]] && return 0
    sleep 0.05
  done
  return 1
}

ensure_daemon() {
  local desired_topic="$1"
  local desired_qos="$2"
  local desired_depth="$3"
  local desired_direct desired_sig
  desired_direct="$(want_direct_flag)"
  desired_sig="$(daemon_signature || true)"

  ensure_state_dir

  local running_mode running_topic running_qos running_depth running_direct running_rate running_sig
  running_mode="$(meta_get mode || true)"
  running_topic="$(meta_get topic || true)"
  running_qos="$(meta_get qos || true)"
  running_depth="$(meta_get depth || true)"
  running_direct="$(meta_get direct || true)"
  running_rate="$(meta_get rate || true)"
  running_sig="$(meta_get daemon_sig || true)"

  if publisher_running; then
    if [[ "$running_mode" == "daemon" ]] \
      && [[ "$running_topic" == "$desired_topic" ]] \
      && [[ "$running_qos" == "$desired_qos" ]] \
      && [[ "$running_depth" == "$desired_depth" ]] \
      && [[ "$running_direct" == "$desired_direct" ]] \
      && [[ "$running_rate" == "$daemon_rate" ]] \
      && [[ "$running_sig" == "$desired_sig" ]]; then
      DAEMON_STARTED_NEW=0
      return 0
    fi
  fi

  stop_publisher

  DAEMON_STARTED_NEW=1
  rm -f "$ready_file" 2>/dev/null || true

  printf "mode=daemon\ntopic=%s\nqos=%s\ndepth=%s\ndirect=%s\nrate=%s\ndaemon_sig=%s\ncmd_file=%s\n" \
    "$desired_topic" "$desired_qos" "$desired_depth" "$desired_direct" "$daemon_rate" "$desired_sig" "$cmd_file" >"$meta_file"
  nohup "$daemon_python" "$daemon_script" \
    --topic "$desired_topic" --qos "$desired_qos" --depth "$desired_depth" --rate "$daemon_rate" \
    --cmd-file "$cmd_file" --ready-file "$ready_file" --idle-exit "$daemon_idle_exit" \
    >"$log_file" 2>&1 &
  echo $! >"$pid_file"

  # If it failed to start (e.g. rclpy missing), clean state and fallback to legacy.
  sleep 0.05
  if ! publisher_running; then
    rm -f "$pid_file" "$meta_file" 2>/dev/null || true
    return 1
  fi
  return 0
}

get_sub_count() {
  local t="$1"
  local out
  out="$(ros2 topic info -v "$t" 2>/dev/null || true)"
  awk -F': ' '/^Subscription count:/{print $2; exit}' <<<"$out" | tr -d '\r' || true
}

auto_pick_topic() {
  if $force_direct; then
    echo "/cmd_vel"
    return
  fi

  local sub_cmd_vel sub_cmd_vel_cmd
  sub_cmd_vel="$(get_sub_count /cmd_vel)"
  sub_cmd_vel_cmd="$(get_sub_count /cmd_vel_cmd)"
  sub_cmd_vel="${sub_cmd_vel:-0}"
  sub_cmd_vel_cmd="${sub_cmd_vel_cmd:-0}"

  if [[ "$sub_cmd_vel_cmd" =~ ^[1-9] ]]; then
    echo "/cmd_vel_cmd"
    return
  fi
  if [[ "$sub_cmd_vel" =~ ^[1-9] ]]; then
    echo "/cmd_vel"
    return
  fi

  # No subscribers detected (likely not connected yet). Default to /cmd_vel.
  echo "/cmd_vel"
}

auto_pick_qos_reliability() {
  local t="$1"
  local out rels
  out="$(ros2 topic info -v "$t" 2>/dev/null || true)"
  rels="$(
    awk '
      $0 ~ /^Endpoint type: SUBSCRIPTION$/ {in_sub=1; next}
      $0 ~ /^Endpoint type:/ {in_sub=0}
      in_sub && $1=="Reliability:" {print $2}
    ' <<<"$out"
  )"

  # Prefer BEST_EFFORT when present (micro-ROS chassis typically requires it).
  if grep -q "BEST_EFFORT" <<<"$rels" 2>/dev/null; then
    echo "best_effort"
    return
  fi
  if grep -q "RELIABLE" <<<"$rels" 2>/dev/null; then
    echo "reliable"
    return
  fi

  # Fallback: prefer BEST_EFFORT (more compatible with micro-ROS chassis).
  echo "best_effort"
}

action="${1:-}"
if [[ -z "${action}" ]]; then
  usage
  exit 0
fi
if [[ "${action}" == "status" ]]; then
  if publisher_running; then
    pid="$(read_pid)"
    echo "RUNNING pid=${pid}"
    if [[ -f "$meta_file" ]]; then
      cat "$meta_file" 2>/dev/null || true
    fi
  else
    echo "STOPPED"
  fi
  exit 0
fi

if [[ "${action}" == "warmup" ]]; then
  topic="$(auto_pick_topic)"
  qos_reliability="$(auto_pick_qos_reliability "$topic")"

  if should_use_daemon && ensure_daemon "$topic" "$qos_reliability" "$qos_depth"; then
    if [[ "$DAEMON_STARTED_NEW" == "1" ]]; then
      wait_for_daemon_ready 80 || true
    fi
    write_cmd_json "0.0" "0.0" "0.0"
    echo "WARMED topic=${topic} qos=${qos_reliability} rate=${daemon_rate}"
    exit 0
  fi

  echo "WARN: 后台发布器不可用，改用 ros2 topic pub 发送 stop 预热。" >&2
  stop_msg_local='{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}'
  ros2 topic pub --once --qos-reliability best_effort --qos-depth "$qos_depth" \
    "$topic" geometry_msgs/msg/Twist "$stop_msg_local" >/dev/null 2>&1 || true
  ros2 topic pub --once --qos-reliability reliable --qos-depth "$qos_depth" \
    "$topic" geometry_msgs/msg/Twist "$stop_msg_local" >/dev/null 2>&1 || true
  exit 0
fi

shift || true

speed="${1:-}"
duration_sec="${2:-}"

default_linear="0.15"
default_angular="0.30"

vx="0.0"
vy="0.0"
wz="0.0"

case "${action}" in
  fwd|forward)
    vx="${speed:-$default_linear}"
    ;;
  back|backward)
    vx="-${speed:-$default_linear}"
    ;;
  left)
    vy="${speed:-$default_linear}"
    ;;
  right)
    vy="-${speed:-$default_linear}"
    ;;
  ccw|left_turn)
    wz="${speed:-$default_angular}"
    ;;
  cw|right_turn)
    wz="-${speed:-$default_angular}"
    ;;
  stop)
    ;;
  *)
    echo "ERROR: 未知动作: ${action}"
    echo
    usage
    exit 2
    ;;
esac

stop_msg='{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}'

send_stop_on_topic() {
  local t="$1"
  local rel="$2"
  # Publish for a short window to avoid "publish once but not matched yet" issues.
  if command -v timeout >/dev/null 2>&1; then
    timeout 0.25s ros2 topic pub -r 20 \
      --qos-reliability "$rel" --qos-depth "$qos_depth" \
      "$t" geometry_msgs/msg/Twist "$stop_msg" >/dev/null 2>&1 || true
  else
    ros2 topic pub --once \
      --qos-reliability "$rel" --qos-depth "$qos_depth" \
      "$t" geometry_msgs/msg/Twist "$stop_msg" >/dev/null 2>&1 || true
  fi
}

send_stop() {
  # Try both common topics and both reliabilities for maximum compatibility.
  send_stop_on_topic "/cmd_vel" "reliable"
  send_stop_on_topic "/cmd_vel" "best_effort"
  send_stop_on_topic "/cmd_vel_cmd" "reliable"
  send_stop_on_topic "/cmd_vel_cmd" "best_effort"
}

use_cached_meta=false
if publisher_running; then
  if [[ "$(meta_get mode || true)" == "daemon" ]]; then
    cached_direct="$(meta_get direct || true)"
    cached_sig="$(meta_get daemon_sig || true)"
    current_sig="$(daemon_signature || true)"
    if [[ "$cached_direct" == "$(want_direct_flag)" && "$cached_sig" == "$current_sig" ]]; then
      cached_topic="$(meta_get topic || true)"
      cached_qos="$(meta_get qos || true)"
      cached_depth="$(meta_get depth || true)"
      if [[ -n "${cached_topic}" && -n "${cached_qos}" && -n "${cached_depth}" ]]; then
        topic="$cached_topic"
        qos_reliability="$cached_qos"
        qos_depth="$cached_depth"
        use_cached_meta=true
      fi
    fi
  fi
fi

if ! $use_cached_meta; then
  topic="$(auto_pick_topic)"
  qos_reliability="$(auto_pick_qos_reliability "$topic")"
fi

if ! $use_cached_meta; then
  sub_count="$(get_sub_count "$topic")"
  sub_count="${sub_count:-0}"
  if [[ "$sub_count" == "0" ]]; then
    echo "WARN: 未检测到 $topic 的订阅者（底盘可能尚未连接/电机未使能）。"
    echo "      请确认已启动 micro_ros_agent，并在 RT-Thread 里运行 microros_chassis + chassis_car_app。"
  fi
fi

msg="{linear: {x: ${vx}, y: ${vy}, z: 0.0}, angular: {x: 0.0, y: 0.0, z: ${wz}}}"

if [[ "${action}" == "stop" || "${action}" == "shutdown" ]]; then
  running_mode="$(meta_get mode || true)"
  if [[ "$running_mode" == "daemon" ]]; then
    write_cmd_json "0.0" "0.0" "0.0"
    if [[ "${action}" == "shutdown" ]]; then
      stop_publisher
      send_stop
      echo "SHUTDOWN"
    elif ! publisher_running; then
      send_stop
    fi
  else
    stop_publisher
    send_stop
    if [[ "${action}" == "shutdown" ]]; then
      echo "SHUTDOWN"
    fi
  fi
  exit 0
fi

echo "action=${action} speed=${speed:-default} duration=${duration_sec:-bg} topic=${topic} qos=${qos_reliability}"

if should_use_daemon; then
  if ensure_daemon "$topic" "$qos_reliability" "$qos_depth"; then
    if [[ -n "${duration_sec}" ]]; then
      # If daemon was just started, wait for it to be ready before counting duration.
      if [[ "$DAEMON_STARTED_NEW" == "1" ]]; then
        wait_for_daemon_ready 80 || true
        # Give DDS discovery some time so short timed moves don't get "eaten" by startup.
        sleep "$startup_wait" || true
      fi
      write_cmd_json "$vx" "$vy" "$wz"
      trap 'write_cmd_json "0.0" "0.0" "0.0"' EXIT INT TERM
      sleep "${duration_sec}"
      write_cmd_json "0.0" "0.0" "0.0"
      exit 0
    fi

    write_cmd_json "$vx" "$vy" "$wz"
    if $hold_mode; then
      trap 'write_cmd_json "0.0" "0.0" "0.0"' EXIT INT TERM
      while true; do
        sleep 3600
      done
    fi
    exit 0
  else
    echo "WARN: 后台发布器启动失败，将使用旧模式。可查看日志：$log_file" >&2
    tail -n 5 "$log_file" 2>/dev/null || true
  fi
fi

if [[ -n "${duration_sec}" ]]; then
  stop_publisher
  trap send_stop EXIT INT TERM
  if command -v timeout >/dev/null 2>&1; then
    timeout "${duration_sec}"s ros2 topic pub -r 10 \
      --qos-reliability "$qos_reliability" --qos-depth "$qos_depth" \
      "$topic" geometry_msgs/msg/Twist "$msg" >/dev/null 2>&1 || true
  else
    # Fallback: run without timeout (user can Ctrl+C)
    ros2 topic pub -r 10 \
      --qos-reliability "$qos_reliability" --qos-depth "$qos_depth" \
      "$topic" geometry_msgs/msg/Twist "$msg"
  fi
else
  if $hold_mode; then
    stop_publisher
    trap send_stop EXIT INT TERM
    ros2 topic pub -r 10 \
      --qos-reliability "$qos_reliability" --qos-depth "$qos_depth" \
      "$topic" geometry_msgs/msg/Twist "$msg"
  else
    # Default: run in background so the terminal returns immediately.
    stop_publisher
    ensure_state_dir
    printf "topic=%s\nqos=%s\ndepth=%s\naction=%s\n" "$topic" "$qos_reliability" "$qos_depth" "$action" >"$meta_file"
    nohup ros2 topic pub -r 10 \
      --qos-reliability "$qos_reliability" --qos-depth "$qos_depth" \
      "$topic" geometry_msgs/msg/Twist "$msg" >"$log_file" 2>&1 &
    echo $! >"$pid_file"
    echo "STARTED pid=$(cat "$pid_file")  (停止: bash car_cmd.sh stop)"
  fi
fi
