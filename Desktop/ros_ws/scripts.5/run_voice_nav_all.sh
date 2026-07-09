#!/usr/bin/env bash
# 语音导览导航 — 总启动（时序对齐 slam_mapping.sh）
# 终端1=语音  2=底盘  3=MicroROS  4=flask  5=Nav2
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"
# shellcheck source=voice_nav_audio.sh
source "${SCRIPT_DIR}/voice_nav_audio.sh"
if [[ -f "${SCRIPT_DIR}/voice_nav_env.sh" ]]; then
  # shellcheck source=voice_nav_env.sh
  source "${SCRIPT_DIR}/voice_nav_env.sh"
  ai_car_normalize_voice_nav_env
fi

AI_CAR_ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS
export VOICE_WAKE_KEYWORDS_FILE="${VOICE_WAKE_KEYWORDS_FILE:-${AI_CAR_ROS_WS}/voice_nav/data/wake_keywords.txt}"
MICROROS_WS="${AI_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LLM_DIR="${AI_CAR_LLM_DIR:-${RKSDK}/test_rkllm_run}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

_ai_car_voice_exit() {
  echo -e "\n${YELLOW}[退出] 语音终端已停止（MicroROS Agent / 底盘 minicom 保持运行）${NC}"
  exit 0
}
trap _ai_car_voice_exit INT TERM

export AI_CAR_MICROROS_IN_TERMINAL="${AI_CAR_MICROROS_IN_TERMINAL:-1}"

export DISPLAY="${DISPLAY:-:0}"
export CAR_CMD="${CAR_CMD:-${AI_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"
export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export AI_CAR_LLM_PATH="${AI_CAR_LLM_PATH:-/rkllm_chat}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 语音导览导航 — 一键启动${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}时序: 编译→USB→MicroROS→底盘→flask→Nav2(固定等待)→switcher→bridge就绪→语音(回车PTT)${NC}"
if declare -F voice_nav_env_info >/dev/null 2>&1; then
  voice_nav_env_info
fi
echo ""

bash "${SCRIPT_DIR}/install_voice_nav.sh"

WAKE_KWS_DIR="${VOICE_WAKE_MODEL_DIR:-${HOME}/Desktop/rk3588-offline-bundle/model/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile}"
if [[ ! -f "${WAKE_KWS_DIR}/tokens.txt" ]] && [[ "${VOICE_WAKE_ENABLED:-1}" != "0" ]]; then
  echo -e "${YELLOW}==== 安装唤醒词 KWS 模型 ====${NC}"
  bash "${SCRIPT_DIR}/install_wake_kws_model.sh" || echo -e "${YELLOW}[警告] KWS 安装失败${NC}"
fi
export VOICE_WAKE_MODEL_DIR="${WAKE_KWS_DIR}"

echo -e "\n${YELLOW}==== [1] 编译 ROS2 工作空间 (同 slam_mapping) ====${NC}"
cd "${AI_CAR_ROS_WS}" || exit 1
if [[ "${VOICE_NAV_SKIP_BUILD:-0}" != "1" ]]; then
  colcon build || echo -e "${YELLOW}编译有警告，继续…${NC}"
else
  echo "  跳过编译 (VOICE_NAV_SKIP_BUILD=1)"
fi
ai_car_source_ros
echo -e "${GREEN}环境加载完成${NC}"

ai_car_start_stack "${SCRIPT_DIR}" "${MICROROS_WS}" "${AI_CAR_ROS_WS}" "${RKSDK}" "${LLM_DIR}"

echo ""
echo -e "${YELLOW}==== [7] 终端5 — Nav2 + smart_switcher (对齐 start_multi_map) ====${NC}"
export VOICE_NAV_SKIP_BUILD=1
export VOICE_NAV_SKIP_USB=1
export VOICE_NAV_SKIP_SENSORS=1
export VOICE_NAV_USE_DEPTH_NAV="${VOICE_NAV_USE_DEPTH_NAV:-0}"
export VOICE_NAV_AUTO_INITIALPOSE="${VOICE_NAV_AUTO_INITIALPOSE:-0}"
export VOICE_NAV_OPEN_RVIZ="${VOICE_NAV_OPEN_RVIZ:-false}"

if pgrep -f "smart_switcher" >/dev/null 2>&1; then
  echo "  smart_switcher 已在运行"
else
  ai_car_stop_prewarm_sensor_launches
  ai_car_open_terminal "终端5-导航栈" \
    "$(ai_car_nav_terminal_preamble "${SCRIPT_DIR}") \
     $(ai_car_voice_nav_stack_env) \
     export AI_CAR_ROS_WS='${AI_CAR_ROS_WS}'; \
     bash '${SCRIPT_DIR}/ai_car_nav_term.sh'"
  sleep 2
fi

echo ""
echo -e "${YELLOW}==== [8] 等待 nav_action_bridge 就绪后再启语音 ====${NC}"
ai_car_wait_nav_stack_ready "${VOICE_NAV_NAV_READY_TIMEOUT:-25}" || true

ai_car_source_ros
if [[ -f "${CAR_CMD}" ]]; then
  bash "${CAR_CMD}" warmup 2>/dev/null || true
fi

SHERPA_VENV="${SHERPA_VENV:-${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate}"
if [[ ! -f "${SHERPA_VENV}" ]]; then
  echo "[错误] 未找到 Sherpa venv"
  exit 1
fi
# shellcheck source=/dev/null
source "${SHERPA_VENV}"

if [[ "${VOICE_NAV_SKIP_AUDIO_CACHE:-0}" != "1" ]]; then
  echo -e "${YELLOW}==== [8b] 语音状态缓存 wav ====${NC}"
  python "${SCRIPT_DIR}/build_voice_audio_cache.py" 2>/dev/null \
    || echo -e "${YELLOW}[警告] 音频缓存未生成，状态播报将降级 espeak${NC}"
fi

echo ""
echo -e "${YELLOW}==== [9] 大模型自检 ====${NC}"
if ! ai_car_voice_nav_startup_probe "${AI_CAR_ROS_WS}"; then
  echo "[错误] 大模型未就绪"
  exit 1
fi
export VOICE_NAV_STARTUP_DONE=1

echo ""
echo -e "${YELLOW}==== [10] 语音播报 + 麦克风 ====${NC}"
voice_nav_setup_playback
AUDIO_DEV="$(ai_car_detect_astra_device_or_die)"

echo ""
echo -e "${GREEN}终端1 — 语音导览/导航（回车触发一句）${NC}"
echo -e "${CYAN}麦克风: ${AUDIO_DEV}${NC}"
echo -e "${CYAN}交互: 唤醒词 -> 回车(开始录) -> 说话 -> 回车(上传LLM)${NC}"
export AI_CAR_AUDIO_DEV="${AUDIO_DEV}"
ai_car_run_stt "${AUDIO_DEV}" "${SCRIPT_DIR}/voice_to_nav_agent.py"
