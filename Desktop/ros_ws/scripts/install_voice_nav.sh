#!/usr/bin/env bash
# 安装语音导览导航桌面入口（不修改原 NovaJoy-大模型控车.sh）
set -eu
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ai_car_common.sh
source "${SCRIPT_DIR}/ai_car_common.sh"

ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
DESKTOP="${UI_DESKTOP_DIR:-${HOME}/Desktop}"

chmod +x "${SCRIPT_DIR}/voice_to_nav_agent.py" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/voice_nav_text.py" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/run_voice_nav_all.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/start_nav_stack_light.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/ai_car_voice_nav_term.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/ai_car_nav_term.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/voice_nav_audio.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/install_wake_kws_model.sh" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/build_voice_audio_cache.py" 2>/dev/null || true

SHERPA_VENV="${SHERPA_VENV:-${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate}"
if [[ -f "${SHERPA_VENV}" ]]; then
  # shellcheck source=/dev/null
  source "${SHERPA_VENV}"
  if ! python -c "import paho.mqtt.client" 2>/dev/null; then
    echo "安装 voice_nav 依赖 paho-mqtt ..."
    pip install -q paho-mqtt
  fi
  if [[ "${VOICE_NAV_SKIP_AUDIO_CACHE:-0}" != "1" ]]; then
    echo "构建语音状态缓存 wav (espeak-ng)..."
    python "${SCRIPT_DIR}/build_voice_audio_cache.py" || echo "[警告] 音频缓存构建失败，运行时将降级 espeak"
  fi
fi

LAUNCHER="${DESKTOP}/NovaJoy-语音导览导航.sh"
{
  echo '#!/usr/bin/env bash'
  echo 'export DISPLAY="${DISPLAY:-:0}"'
  echo "export CAR_CMD=\"${ROS_WS}/car_cmd.sh\""
  echo 'export CAR_CMD_PYTHON="/usr/bin/python3"'
  echo "exec bash \"${SCRIPT_DIR}/run_voice_nav_all.sh\""
} > "${LAUNCHER}"
chmod +x "${LAUNCHER}" 2>/dev/null || true

echo "语音导览导航已安装。"
echo "  一键启动: bash ${SCRIPT_DIR}/run_voice_nav_all.sh"
echo "  TTS 测试: bash -lc 'source ${SHERPA_VENV}; source ${SCRIPT_DIR}/voice_nav_audio.sh; voice_nav_setup_playback; cd ${ROS_WS} && python3 scripts/test_sherpa_tts.py'"
echo "  文字测试: cd ${ROS_WS} && python3 scripts/voice_nav_text.py"
echo "  桌面:     ${LAUNCHER}"
echo ""
echo "原大模型控车入口不变: NovaJoy-大模型控车.sh → run_ai_car_all.sh"
