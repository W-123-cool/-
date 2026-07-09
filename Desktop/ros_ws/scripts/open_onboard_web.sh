#!/usr/bin/env bash
# Open onboard web UI in system browser (on demand, not kiosk).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=onboard_api_base.sh
source "${SCRIPT_DIR}/onboard_api_base.sh"
_onboard_load_config || true
if [[ -f "${SCRIPT_DIR}/voice_nav_env.sh" ]]; then
  # shellcheck source=voice_nav_env.sh
  source "${SCRIPT_DIR}/voice_nav_env.sh"
fi

API_BASE="$(_onboard_api_base)"
TAB="${ONBOARD_DEFAULT_TAB:-tour}"
URL="${API_BASE}/onboard?tab=${TAB}"
export DISPLAY="${DISPLAY:-:0}"

echo "Open onboard UI: ${URL}"

if ! _onboard_api_require; then
  echo ""
  echo "Or open on PC browser: ${URL}"
  exit 1
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "${URL}" >/dev/null 2>&1 &
elif command -v chromium-browser >/dev/null 2>&1; then
  chromium-browser "${URL}" >/dev/null 2>&1 &
elif command -v chromium >/dev/null 2>&1; then
  chromium "${URL}" >/dev/null 2>&1 &
else
  echo "[error] No browser (xdg-open / chromium). Open manually:"
  echo "  ${URL}"
  exit 1
fi
