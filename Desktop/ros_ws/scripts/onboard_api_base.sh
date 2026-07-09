#!/usr/bin/env bash
# Resolve PC backend URL for onboard web UI.
# Vehicle must NOT use 127.0.0.1 when FastAPI runs on a PC.
set -euo pipefail

_onboard_config_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

_onboard_load_config() {
  local dir cfg
  dir="$(_onboard_config_dir)"
  for cfg in \
    "${dir}/onboard_api.env" \
    "${HOME}/.novajoy/onboard_api.env"; do
    if [[ -f "${cfg}" ]]; then
      # shellcheck source=/dev/null
      source "${cfg}"
      return 0
    fi
  done
  return 1
}

_onboard_api_base() {
  local base="${COURIER_API_BASE:-${VOICE_TOUR_API_BASE:-${ONBOARD_API_BASE:-http://127.0.0.1:8000}}}"
  echo "${base%/}"
}

_onboard_is_localhost() {
  local base="$1"
  [[ "${base}" =~ ^https?://(127\.0\.0\.1|localhost)(:[0-9]+)?/?$ ]]
}

_onboard_api_check() {
  local base url code
  base="$(_onboard_api_base)"
  url="${base}/api/health"
  if ! command -v curl >/dev/null 2>&1; then
    return 1
  fi
  code="$(curl -sf --max-time 5 -o /dev/null -w '%{http_code}' "${url}" 2>/dev/null || echo "000")"
  [[ "${code}" == "200" ]]
}

_onboard_api_warn_localhost() {
  local base dir example
  base="$(_onboard_api_base)"
  if ! _onboard_is_localhost "${base}"; then
    return 0
  fi
  dir="$(_onboard_config_dir)"
  example="${dir}/onboard_api.env.example"
  echo "[warn] API base is ${base} ? this is the vehicle itself, not your PC backend." >&2
  echo "  Fix (pick one):" >&2
  echo "    1) Edit ${dir}/onboard_api.env  (copy from onboard_api.env.example)" >&2
  echo "    2) export COURIER_API_BASE=http://<PC_LAN_IP>:8000" >&2
  if [[ -f "${example}" ]]; then
    echo "  Example: cp onboard_api.env.example onboard_api.env" >&2
  fi
  return 1
}

_onboard_api_require() {
  local base
  base="$(_onboard_api_base)"
  if ! _onboard_api_warn_localhost; then
    return 1
  fi
  if _onboard_api_check; then
    echo "[ok] backend reachable: ${base}" >&2
    return 0
  fi
  echo "[error] cannot reach ${base}/api/health" >&2
  echo "  PC:  uvicorn main:app --host 0.0.0.0 --port 8000" >&2
  echo "  PC:  allow TCP 8000 in Windows firewall" >&2
  echo "  Car: curl -v ${base}/api/health" >&2
  echo "  Car: export COURIER_API_BASE=http://<PC_LAN_IP>:8000" >&2
  return 1
}
