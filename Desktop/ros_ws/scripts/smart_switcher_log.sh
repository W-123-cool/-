#!/usr/bin/env bash
# smart_switcher ????????????????? ~/logs/smart_switcher/smart_switcher_YYYYMMDD_HHMMSS.log
# ???????? N ??????? 5??????? source?????????????/??????
#
# ???????
#   SMART_SWITCHER_LOG_DIR   ???????????? ~/logs/smart_switcher??
#   SMART_SWITCHER_LOG_KEEP  ????????????? 5??
#
# ?????
#   source scripts/smart_switcher_log.sh
#   ai_car_start_smart_switcher /path/to/ros_ws    # ???????????????
#   ai_car_tail_smart_switcher_log                 # tail -f ???????
#   ai_car_list_smart_switcher_logs                # ??????????

ai_car_switcher_log_dir() {
  local d="${SMART_SWITCHER_LOG_DIR:-${HOME}/logs/smart_switcher}"
  mkdir -p "${d}"
  printf '%s' "${d}"
}

ai_car_prune_switcher_logs() {
  local dir keep count f
  dir="$(ai_car_switcher_log_dir)"
  keep="${SMART_SWITCHER_LOG_KEEP:-5}"
  [[ "${keep}" =~ ^[0-9]+$ ]] || keep=5
  (( keep < 1 )) && keep=1

  shopt -s nullglob
  local -a files=( "${dir}"/smart_switcher_*.log )
  shopt -u nullglob
  (( ${#files[@]} <= keep )) && return 0

  count=0
  while IFS= read -r f; do
    [[ -n "${f}" ]] || continue
    count=$((count + 1))
    if (( count > keep )); then
      rm -f "${f}"
    fi
  done < <(ls -1t "${dir}"/smart_switcher_*.log 2>/dev/null || true)
}

ai_car_write_switcher_session_header() {
  local log_file="$1"
  local ws="$2"
  {
    echo "======== smart_switcher session ========"
    echo "started: $(date '+%Y-%m-%dT%H:%M:%S %z' 2>/dev/null || date)"
    echo "host: $(hostname 2>/dev/null || echo unknown)"
    echo "ros_ws: ${ws:-?}"
    echo "robot_id: ${MQTT_ROBOT_ID:-${VOICE_NAV_ROBOT_ID:-robot01}}"
    echo "pid_file: $(ai_car_switcher_log_dir)/smart_switcher.pid"
    echo "log_file: ${log_file}"
    echo "hint: tail -f $(ai_car_switcher_log_dir)/latest.log"
    echo "search: grep -E 'waiting_receipt|Cross floor|after_wake|deliver_room' ${log_file}"
    echo "========================================"
  } >> "${log_file}"
}

# ??????? smart_switcher??stdout/stderr ?????????echo ??? PID
ai_car_start_smart_switcher() {
  local ws="${1:-${AI_CAR_ROS_WS:-}}"
  local log_dir log_file pid nice_prefix

  if pgrep -f "smart_switcher" >/dev/null 2>&1; then
    echo -e "${YELLOW:-}[smart_switcher] ?????????????????${NC:-}" >&2
    if [[ -f "$(ai_car_switcher_log_dir)/smart_switcher.pid" ]]; then
      cat "$(ai_car_switcher_log_dir)/smart_switcher.pid"
    else
      pgrep -f "smart_switcher" | head -1
    fi
    return 0
  fi

  log_dir="$(ai_car_switcher_log_dir)"
  log_file="${log_dir}/smart_switcher_$(date +%Y%m%d_%H%M%S).log"
  ai_car_prune_switcher_logs
  : > "${log_file}"
  ai_car_write_switcher_session_header "${log_file}" "${ws}"
  ln -sfn "${log_file}" "${log_dir}/latest.log"
  printf '%s\n' "${log_file}" > "${log_dir}/latest.path"

  if [[ -n "${ws}" && -f "${ws}/install/setup.bash" ]]; then
    # shellcheck source=/dev/null
    source "${ws}/install/setup.bash"
  fi

  nice_prefix=""
  if declare -F ai_car_nice_prefix >/dev/null 2>&1; then
    nice_prefix="$(ai_car_nice_prefix)"
  fi

  ${nice_prefix}env SMART_NAV_ACTION_WAIT_SEC="${SMART_NAV_ACTION_WAIT_SEC:-30}" \
    AI_CAR_ROS_WS="${ws}" \
    ros2 run smart_nav_manager smart_switcher >> "${log_file}" 2>&1 &
  pid=$!
  echo "${pid}" > "${log_dir}/smart_switcher.pid"

  echo -e "${GREEN:-}[smart_switcher] PID ${pid}  ????: ${log_file}${NC:-}" >&2
  echo -e "${CYAN:-}[smart_switcher] ?????: tail -f ${log_dir}/latest.log${NC:-}" >&2
  printf '%s' "${pid}"
}

ai_car_list_smart_switcher_logs() {
  local dir
  dir="$(ai_car_switcher_log_dir)"
  echo "???????: ${dir}  (??????? ${SMART_SWITCHER_LOG_KEEP:-5} ???)"
  ls -lt "${dir}"/smart_switcher_*.log 2>/dev/null || echo "(???)"
  if [[ -L "${dir}/latest.log" ]]; then
    echo "latest -> $(readlink -f "${dir}/latest.log" 2>/dev/null || readlink "${dir}/latest.log")"
  fi
}

ai_car_tail_smart_switcher_log() {
  local dir="${SMART_SWITCHER_LOG_DIR:-${HOME}/logs/smart_switcher}"
  if [[ ! -e "${dir}/latest.log" ]]; then
    echo "???? smart_switcher ????????????????" >&2
    return 1
  fi
  tail -f "${dir}/latest.log"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-list}" in
    start)
      shift || true
      ai_car_start_smart_switcher "${1:-${AI_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}}"
      ;;
    tail|follow|-f)
      ai_car_tail_smart_switcher_log
      ;;
    list|ls)
      ai_car_list_smart_switcher_logs
      ;;
    prune)
      ai_car_prune_switcher_logs
      ai_car_list_smart_switcher_logs
      ;;
    *)
      echo "???: $0 {start [ros_ws]|tail|list|prune}"
      exit 1
      ;;
  esac
fi
