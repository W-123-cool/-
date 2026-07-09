#!/usr/bin/env bash
# Tour product: voice (background) + web onboard UI on PC backend (no Kivy).
# Requires: PC backend up with /onboard; Nav2 + smart_switcher (run_voice_nav_all.sh).
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

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

API_BASE="${COURIER_API_BASE:-${VOICE_TOUR_API_BASE:-http://127.0.0.1:8000}}"
API_BASE="${API_BASE%/}"
export COURIER_API_BASE="${API_BASE}"
export VOICE_TOUR_API_BASE="${API_BASE}"
export VOICE_TOUR_REQUIRE_API="${VOICE_TOUR_REQUIRE_API:-1}"
export VOICE_INPUT_MODE="${VOICE_INPUT_MODE:-ui}"
export ONBOARD_DEFAULT_TAB="${ONBOARD_DEFAULT_TAB:-tour}"
export DISPLAY="${DISPLAY:-:0}"

VOICE_LOG="${VOICE_NAV_LOG:-/tmp/voice_nav.log}"
ONBOARD_WEB_URL="${API_BASE}/onboard?tab=${ONBOARD_DEFAULT_TAB}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy tour integrated startup${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}  API: ${API_BASE}${NC}"
echo -e "${CYAN}  Web UI: ${ONBOARD_WEB_URL}${NC}"
echo ""

if ! curl -sf --max-time 5 "${API_BASE}/api/health" >/dev/null 2>&1; then
  echo -e "${RED}[error] Backend unreachable: ${API_BASE}${NC}"
  echo "  Start on PC: python -m uvicorn main:app --host 0.0.0.0 --port 8000"
  exit 1
fi
echo -e "${GREEN}[ok] Backend health${NC}"

if ! curl -sf --max-time 5 "${API_BASE}/onboard" >/dev/null 2>&1; then
  echo -e "${YELLOW}[warn] /onboard not found — sync frontend/onboard.html to PC backend${NC}"
else
  echo -e "${GREEN}[ok] onboard web page${NC}"
fi

if ! pgrep -f "smart_switcher" >/dev/null 2>&1; then
  echo -e "${YELLOW}[warn] smart_switcher not running${NC}"
  echo "  Run first: bash ${SCRIPT_DIR}/run_voice_nav_all.sh"
  read -r -p "Continue with voice only? [y/N] " ans || true
  if [[ "${ans,,}" != "y" ]]; then
    exit 1
  fi
else
  echo -e "${GREEN}[ok] smart_switcher running${NC}"
fi

SHERPA_VENV="${SHERPA_VENV:-${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate}"
if [[ ! -f "${SHERPA_VENV}" ]]; then
  echo -e "${RED}[error] Sherpa venv not found: ${SHERPA_VENV}${NC}"
  exit 1
fi

if pgrep -f "voice_to_nav_agent" >/dev/null 2>&1; then
  echo -e "${GREEN}[ok] voice_to_nav_agent already running${NC}"
else
  echo -e "${YELLOW}==== start voice_to_nav_agent (background) ====${NC}"
  # shellcheck source=/dev/null
  source "${SHERPA_VENV}"
  export VOICE_NAV_STARTUP_DONE="${VOICE_NAV_STARTUP_DONE:-1}"
  export VOICE_NAV_SKIP_BUILD=1
  export VOICE_NAV_USE_LLM="${VOICE_NAV_USE_LLM:-0}"
  export VOICE_INPUT_MODE="${VOICE_INPUT_MODE:-ui}"
  voice_nav_setup_playback 2>/dev/null || true
  if [[ -z "${AI_CAR_AUDIO_DEV:-}" ]]; then
    if AUDIO_DEV="$(ai_car_detect_astra_device_or_die 2>/dev/null)"; then
      export AI_CAR_AUDIO_DEV="${AUDIO_DEV}"
    fi
  fi
  nohup python "${SCRIPT_DIR}/voice_to_nav_agent.py" >>"${VOICE_LOG}" 2>&1 &
  sleep 2
  if pgrep -f "voice_to_nav_agent" >/dev/null 2>&1; then
    echo -e "${GREEN}[ok] voice started -> ${VOICE_LOG}${NC}"
  else
    echo -e "${RED}[error] voice failed, see ${VOICE_LOG}${NC}"
    tail -n 20 "${VOICE_LOG}" 2>/dev/null || true
    exit 1
  fi
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Voice ready. Web UI (open on demand):${NC}"
echo -e "${CYAN}  ${ONBOARD_WEB_URL}${NC}"
echo ""
echo "  bash ${SCRIPT_DIR}/open_onboard_web.sh"
echo "  Or set ONBOARD_OPEN_WEB=1 to auto-open browser"
echo -e "${GREEN}========================================${NC}"

if [[ "${ONBOARD_OPEN_WEB:-0}" == "1" ]]; then
  bash "${SCRIPT_DIR}/open_onboard_web.sh"
fi
