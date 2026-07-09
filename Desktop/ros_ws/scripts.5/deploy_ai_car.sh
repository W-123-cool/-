#!/usr/bin/env bash
# 一键部署大模型控车脚本（仅必改文件，不覆盖 voice_to_ai_car.py / ai_car_chat_term.sh）
#
# 用法（在板子上，与这些 .sh 放在同一目录）:
#   bash deploy_ai_car.sh
#   bash deploy_ai_car.sh --start    # 部署后立即启动
#
# 或从 PC 拷整个 scripts 目录后:
#   bash ~/Desktop/rock_ws/ros_ws/scripts/deploy_ai_car.sh
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${AI_CAR_SCRIPTS_DIR:-${HOME}/Desktop/rock_ws/ros_ws/scripts}"
mkdir -p "${TARGET}"
TARGET="$(cd "${TARGET}" && pwd)"
SAME_DIR=0
[[ "${SRC}" == "${TARGET}" ]] && SAME_DIR=1
DO_START=0
[[ "${1:-}" == "--start" ]] && DO_START=1

# 仅必改 + 新增（不含 voice_to_ai_car.py / ai_car_chat_term.sh / voice_to_llm.py）
REQUIRED=(
  ai_car_common.sh
  ai_car_sudo_askpass.sh
  ai_car_microros_term.sh
  ai_car_chassis_term.sh
  ai_car_llm_server_term.sh
  ai_car_voice_term.sh
  start_ai_car.sh
  run_ai_car_all.sh
  install_ai_car.sh
  deploy_ai_car.sh
  fix_scripts_crlf.sh
)

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 大模型控车 — 一键部署${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}来源: ${SRC}${NC}"
echo -e "${CYAN}目标: ${TARGET}${NC}"
echo ""

missing=0
for f in "${REQUIRED[@]}"; do
  if [[ ! -f "${SRC}/${f}" ]]; then
    echo -e "${RED}[缺少] ${SRC}/${f}${NC}" >&2
    missing=1
    continue
  fi
  if [[ "${SAME_DIR}" -eq 1 ]]; then
    echo -e "  ${GREEN}✓${NC} ${f} (已在目标目录)"
  else
    cp -f "${SRC}/${f}" "${TARGET}/${f}"
    echo -e "  ${GREEN}✓${NC} ${f}"
  fi
done

if [[ "${missing}" -eq 1 ]]; then
  echo ""
  echo -e "${RED}请先把 scripts 目录里上述文件拷到板子同一文件夹，再执行本脚本。${NC}" >&2
  exit 1
fi

chmod +x "${TARGET}"/*.sh 2>/dev/null || true

# 修复 Windows 拷贝带来的 CRLF
echo ""
echo -e "${YELLOW}>>> 修复换行符 (CRLF → LF)…${NC}"
if [[ -f "${TARGET}/fix_scripts_crlf.sh" ]]; then
  bash "${TARGET}/fix_scripts_crlf.sh"
else
  for f in "${TARGET}"/*.sh; do
    [[ -f "${f}" ]] || continue
    sed -i 's/\r$//' "${f}" 2>/dev/null || { tr -d '\r' < "${f}" > "${f}.tmp" && mv "${f}.tmp" "${f}"; }
  done
  echo "  已处理 ${TARGET}/*.sh"
fi

# 可选：同目录有 bootstrap 也一并更新（方便以后 --gen-only）
if [[ -f "${SRC}/bootstrap_ai_car_all.sh" ]]; then
  if [[ "${SAME_DIR}" -eq 0 ]]; then
    cp -f "${SRC}/bootstrap_ai_car_all.sh" "${TARGET}/bootstrap_ai_car_all.sh"
  fi
  chmod +x "${TARGET}/bootstrap_ai_car_all.sh"
  echo -e "  ${GREEN}✓${NC} bootstrap_ai_car_all.sh"
fi

echo ""
echo -e "${YELLOW}>>> 安装（桌面快捷方式 + car_cmd）${NC}"
export AI_CAR_SCRIPTS_DIR="${TARGET}"
export AI_CAR_RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
export AI_CAR_ROS_WS="${AI_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}"
export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"
bash "${TARGET}/install_ai_car.sh"

echo ""
echo -e "${GREEN}部署完成。${NC}"
echo -e "  启动: ${YELLOW}bash ${TARGET}/run_ai_car_all.sh${NC}"
echo -e "  或双击: ~/Desktop/NovaJoy-大模型控车.sh"
echo ""
echo -e "${CYAN}未覆盖（保持板子原样）:${NC} voice_to_ai_car.py, ai_car_chat_term.sh, voice_to_llm.py"

if [[ "${DO_START}" -eq 1 ]]; then
  echo ""
  echo -e "${YELLOW}>>> 启动四终端 + 语音…${NC}"
  exec bash "${TARGET}/run_ai_car_all.sh"
fi
