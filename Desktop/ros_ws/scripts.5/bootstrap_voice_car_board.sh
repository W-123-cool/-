#!/usr/bin/env bash
# 板端引导：同步 install 后一键启动
set -euo pipefail

TARGET="${HOME}/Desktop/rock_ws/ros_ws/scripts"
BOOT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"

mkdir -p "${TARGET}"

if [[ -f "${BOOT_DIR}/run_voice_car_all.sh" ]]; then
  cp -f "${BOOT_DIR}/run_voice_car_all.sh" "${TARGET}/"
  cp -f "${BOOT_DIR}/install_voice_car.sh" "${TARGET}/" 2>/dev/null || true
  cp -f "${BOOT_DIR}/start_voice_car.sh" "${TARGET}/" 2>/dev/null || true
  cp -f "${BOOT_DIR}/voice_car_common.sh" "${TARGET}/" 2>/dev/null || true
  cp -f "${BOOT_DIR}/voice_car_chassis_term.sh" "${TARGET}/" 2>/dev/null || true
  cp -f "${BOOT_DIR}/voice_car_microros_term.sh" "${TARGET}/" 2>/dev/null || true
  cp -f "${BOOT_DIR}/one_shot_voice_car.sh" "${TARGET}/" 2>/dev/null || true
fi

if [[ ! -f "${TARGET}/run_voice_car_all.sh" && ! -f "${TARGET}/install_voice_car.sh" ]]; then
  echo "[错误] 未找到脚本，请从 PC 同步整个 scripts 目录到:"
  echo "  ${TARGET}/"
  exit 1
fi

chmod +x "${TARGET}"/*.sh 2>/dev/null || true
exec bash "${TARGET}/run_voice_car_all.sh"
