#!/usr/bin/env bash
# 终端1 — 语音 → 导览/导航/问答（保留底盘控车）
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

RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS="${ROS_WS}"
SHERPA_VENV="${SHERPA_VENV:-${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate}"

export CAR_CMD="${CAR_CMD:-${ROS_WS}/car_cmd.sh}"
export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export AI_CAR_LLM_PATH="${AI_CAR_LLM_PATH:-/rkllm_chat}"

clear
echo "=============================================="
echo "  终端1 — 语音导览/导航/问答 + 底盘控车"
echo "=============================================="
echo "  LLM:     ${AI_CAR_LLM_HOST}${AI_CAR_LLM_PATH}"
echo "  导航:    MQTT robot/nav_room（需终端5 smart_switcher）"
echo "  底盘:    前进/后退/左转 仍走 aichat"
echo "=============================================="
echo ""

if [[ ! -f "${SHERPA_VENV}" ]]; then
  echo "[错误] 未找到 Sherpa venv: ${SHERPA_VENV}"
  exec bash
fi

if [[ "${VOICE_NAV_USE_LLM:-0}" == "1" ]] && [[ -n "${DASHSCOPE_API_KEY:-}" ]]; then
  echo "[提示] 已配置 DASHSCOPE_API_KEY，本地 flask 可选（启动时会自动探测云端/本地）"
else
  if ! ai_car_wait_llm; then
    echo "[错误] 请先启动终端4 的 flask_server.py"
    exec bash
  fi
fi

# shellcheck source=/dev/null
source "${SHERPA_VENV}"

echo "配置语音播报 (Sherpa Matcha + 3.5mm)…"
voice_nav_setup_playback

AUDIO_DEV="$(ai_car_detect_astra_device_or_die)"
echo "麦克风: ${AUDIO_DEV}"
echo ""

ai_car_run_stt "${AUDIO_DEV}" "${SCRIPT_DIR}/voice_to_nav_agent.py"
