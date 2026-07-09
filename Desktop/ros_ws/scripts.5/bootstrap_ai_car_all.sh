#!/usr/bin/env bash
# =============================================================================
# NovaJoy 大模型控车 + 语音 — 自包含总脚本
# 只需复制本文件到板子，一条命令自动生成全部脚本并启动。
#
# 用法:
#   bash bootstrap_ai_car_all.sh           # 生成文件 + 启动
#   bash bootstrap_ai_car_all.sh --gen-only  # 仅生成，不启动
#   AI_CAR_SCRIPTS_DIR=~/Desktop bash bootstrap_ai_car_all.sh
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

BOOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${AI_CAR_SCRIPTS_DIR:-${HOME}/Desktop/rock_ws/ros_ws/scripts}"
GEN_ONLY=0
[[ "${1:-}" == "--gen-only" ]] && GEN_ONLY=1

mkdir -p "${TARGET}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 大模型控车 — 自动生成${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}输出目录: ${TARGET}${NC}"
echo ""

# 若与仓库 scripts 同目录，优先复制现成文件（更快）
_copy_if_exists() {
  local name="$1"
  local src="${BOOT_DIR}/${name}"
  local dest="${TARGET}/${name}"
  if [[ ! -f "${src}" ]]; then
    return 1
  fi
  if [[ ! -f "${dest}" ]] || [[ "${src}" -nt "${dest}" ]]; then
    cp -f "${src}" "${dest}"
    return 0
  fi
  return 1
}

_gen_ai_car_common() {
  _copy_if_exists ai_car_common.sh && return 0
  _copy_if_exists ai_car_sudo_askpass.sh || true
cat > "${TARGET}/ai_car_sudo_askpass.sh" <<'ASK'
#!/bin/sh
printf '%s\n' "${AI_CAR_SUDO_PASS:-rock}"
ASK
chmod +x "${TARGET}/ai_car_sudo_askpass.sh"
cat > "${TARGET}/ai_car_common.sh" <<'COMMON'
#!/usr/bin/env bash
ai_car_resolve_ros_ws() {
  local script_dir="${1:-}" candidate
  if [[ -n "${AI_CAR_ROS_WS:-}" && -f "${AI_CAR_ROS_WS}/car_cmd.sh" ]]; then echo "${AI_CAR_ROS_WS}"; return 0; fi
  for candidate in "${HOME}/Desktop/rock_ws/ros_ws" "${script_dir}/.." "${HOME}/rock_ws/ros_ws"; do
    if [[ -n "${candidate}" && -f "${candidate}/car_cmd.sh" ]]; then echo "$(cd "${candidate}" && pwd)"; return 0; fi
  done
  echo "${HOME}/Desktop/rock_ws/ros_ws"
}
ai_car_open_terminal() {
  local title="$1" inner_cmd="$2"
  export DISPLAY="${DISPLAY:-:0}"
  if command -v gnome-terminal >/dev/null 2>&1; then gnome-terminal --title="${title}" -- bash -lc "${inner_cmd}; exec bash"; return 0; fi
  if command -v xfce4-terminal >/dev/null 2>&1; then xfce4-terminal --title="${title}" -e "bash -lc '${inner_cmd}; exec bash'"; return 0; fi
  if command -v lxterminal >/dev/null 2>&1; then lxterminal --title="${title}" -e "bash -lc '${inner_cmd}; exec bash'"; return 0; fi
  if command -v xterm >/dev/null 2>&1; then xterm -title "${title}" -e bash -lc "${inner_cmd}; exec bash" & return 0; fi
  echo ""; echo "[提示] 请手动执行: ${inner_cmd}"; echo ""; return 1
}
ai_car_microros_running() { pgrep -f "micro_ros_agent.*8888" >/dev/null 2>&1; }
ai_car_llm_port_open() {
  local host="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}" code
  code="$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' "${host}/" 2>/dev/null || echo 000)"
  [[ "${code}" != "000" && -n "${code}" ]]
}
ai_car_wait_llm() {
  local max="${AI_CAR_LLM_WAIT_SEC:-180}" w=0
  ai_car_llm_port_open && { echo "  LLM 已就绪"; return 0; }
  while ! ai_car_llm_port_open; do sleep 2; w=$((w+2)); ((w>=max)) && return 1; done
  echo "  LLM 已就绪"; return 0
}
ai_car_usb_auto_setup() {
  local ros_ws="$1" sd="${2:-}" setup="${ros_ws}/usb_auto_setup.sh" pass="${AI_CAR_SUDO_PASS:-rock}"
  ( cd "${ros_ws}" && echo "${pass}" | sudo -S -v 2>/dev/null; bash "${setup}" )
}
ai_car_prepare_serial() {
  local ros_ws="$1" sd="${2:-}" serial="${AI_CAR_SERIAL:-/dev/rt_shell}"
  [[ -e "${serial}" ]] && { echo "  串口: ${serial}"; return 0; }
  ai_car_usb_auto_setup "${ros_ws}" "${sd}" || return 1
  [[ -e "${serial}" ]] && return 0; return 1
}
ai_car_send_chassis_cmds() {
  local ip="$1" s="${AI_CAR_SERIAL:-/dev/rt_shell}" b="${AI_CAR_BAUD:-1500000}"
  [[ -e "${s}" ]] || return 1
  stty -F "${s}" "${b}" raw -echo 2>/dev/null || true
  { sleep 0.8; printf "microros_chassis udp %s 8888\r\n" "${ip}"; sleep 3; printf "chassis_car_app\r\n"; } > "${s}" 2>/dev/null
  sleep 4; return 0
}
ai_car_copy_car_cmd() {
  local rksdk="$1" ros_ws="$2" copied=0 f
  for f in car_cmd.sh car_cmd_daemon.py; do
    if [[ -f "${rksdk}/${f}" ]]; then cp -f "${rksdk}/${f}" "${ros_ws}/${f}"; chmod +x "${ros_ws}/${f}" 2>/dev/null || true
      echo "  已复制 ${f} -> ${ros_ws}/"; copied=1; fi
  done
  [[ "${copied}" -eq 0 ]] && echo "[警告] 未在 ${rksdk} 找到 car_cmd 文件" >&2
}
ai_car_detect_astra_device_or_die() {
  local card pulse_src
  card="$(arecord -l 2>/dev/null | grep -iE 'astra|orbbec' | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1)"
  [[ -n "${card}" ]] && { echo "alsa:plughw:${card},0"; return 0; }
  if command -v pactl >/dev/null 2>&1; then
    pulse_src="$(pactl list sources short 2>/dev/null | awk '/[Oo]rbbec|[Aa]stra/ && !/monitor/ {print $2; exit}')"
    [[ -n "${pulse_src}" ]] && { echo "pulse:${pulse_src}"; return 0; }
  fi
  echo "[错误] 未找到 Astra 麦克风" >&2; lsusb | grep -i 2bc5 || true; arecord -l || true; exit 1
}
ai_car_run_stt() {
  local device="$1" py_script="$2"
  case "${device}" in
    alsa:*) exec arecord -D "${device#alsa:}" -f S16_LE -r 16000 -c 1 -t raw | python3 "${py_script}" ;;
    pulse:*) exec parecord --device="${device#pulse:}" --format=s16le --rate=16000 --channels=1 --raw | python3 "${py_script}" ;;
    *) echo "[错误] 未知设备: ${device}" >&2; exit 1 ;;
  esac
}
ai_car_source_ros() {
  set +u; source /opt/ros/foxy/setup.bash
  [[ -f "${AI_CAR_ROS_WS}/install/setup.bash" ]] && source "${AI_CAR_ROS_WS}/install/setup.bash"
  set -u; export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
}
ai_car_start_stack() {
  local script_dir="$1" microros_ws="$2" ros_ws="$3" rksdk="$4" llm_dir="$5" rock_ip
  rock_ip="$(hostname -I | awk '{print $1}')"
  export AI_CAR_MICROROS_WS="${microros_ws}" AI_CAR_ROS_WS="${ros_ws}" AI_CAR_RKSDK="${rksdk}"
  export AI_CAR_LLM_DIR="${llm_dir}" AI_CAR_ROCK_IP="${rock_ip}"
  echo ""; echo "==== [0] 复制 car_cmd ===="; ai_car_copy_car_cmd "${rksdk}" "${ros_ws}"
  export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"
  echo ""; echo "==== [1] 终端3 MicroROS ===="
  if ai_car_microros_running; then echo "  已在运行"; else
    ai_car_open_terminal "终端3-MicroROS" "bash '${script_dir}/ai_car_microros_term.sh'"; sleep 3; fi
  echo ""; echo "==== [2] 终端2 底盘 ===="; ai_car_prepare_serial "${ros_ws}" "${script_dir}" || true
  ai_car_open_terminal "终端2-底盘" "bash '${script_dir}/ai_car_chassis_term.sh'"
  echo ""; echo "  请在终端2 minicom 手动输入:"
  echo "    microros_chassis udp ${rock_ip} 8888"
  echo "    chassis_car_app"
  read -r -p "手动完成、底盘就绪后 Enter…"
  echo ""; echo "==== [3] 终端4 flask :8001 ===="
  if ai_car_llm_port_open; then echo "  已在运行"; else
    ai_car_open_terminal "终端4-LLM" "bash '${script_dir}/ai_car_llm_server_term.sh'"
    ai_car_wait_llm || true; fi
  echo ""; echo "==== [4] 本终端=终端1 语音 ===="
}
COMMON
}

_gen_term_scripts() {
  for f in ai_car_microros_term.sh ai_car_chassis_term.sh ai_car_llm_server_term.sh \
    ai_car_voice_term.sh ai_car_chat_term.sh ai_car_sudo_askpass.sh; do
    _copy_if_exists "${f}" && continue
  done
  [[ -f "${TARGET}/ai_car_microros_term.sh" ]] && return 0

cat > "${TARGET}/ai_car_microros_term.sh" <<'T1'
#!/usr/bin/env bash
MICROROS_WS="${AI_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
clear; echo "终端3 — MicroROS Agent"; echo "cd ${MICROROS_WS}"; echo "ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888"
[[ ! -d "${MICROROS_WS}/install" ]] && echo "[错误] 未找到 microros_ws" && exec bash
set +u; source /opt/ros/foxy/setup.bash; source "${MICROROS_WS}/install/setup.bash"; set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"; cd "${MICROROS_WS}"
exec ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
T1

cat > "${TARGET}/ai_car_chassis_term.sh" <<'T2'
#!/usr/bin/env bash
ROCK_IP="${AI_CAR_ROCK_IP:-$(hostname -I | awk '{print $1}')}"
ROS_WS="${AI_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}"
SERIAL="${AI_CAR_SERIAL:-/dev/rt_shell}"; BAUD="${AI_CAR_BAUD:-1500000}"
USB_SETUP="${ROS_WS}/usb_auto_setup.sh"
clear; echo "终端2 — minicom"; echo "sudo 密码自动(rock)"; echo "RT-Thread: microros_chassis udp ${ROCK_IP} 8888"; echo "           chassis_car_app"
[[ ! -e "${SERIAL}" && -f "${USB_SETUP}" ]] && ( cd "${ROS_WS}" && bash "${USB_SETUP}" ) || true
[[ ! -e "${SERIAL}" ]] && echo "[错误] 无 ${SERIAL}" && exec bash
read -r -p "Enter 启动 minicom…" _; exec minicom -D "${SERIAL}" -b "${BAUD}"
T2

cat > "${TARGET}/ai_car_llm_server_term.sh" <<'T3'
#!/usr/bin/env bash
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LLM_DIR="${AI_CAR_LLM_DIR:-${RKSDK}/test_rkllm_run}"
VENV="${AI_CAR_LLM_VENV:-${RKSDK}/.venv/bin/activate}"
clear; echo "终端4 — flask_server.py :8001"
[[ ! -f "${LLM_DIR}/flask_server.py" ]] && echo "[错误] 未找到 flask_server.py" && exec bash
source "${VENV}"; cd "${LLM_DIR}"; exec python flask_server.py
T3

cat > "${TARGET}/ai_car_chat_term.sh" <<'T4'
#!/usr/bin/env bash
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LLM_DIR="${AI_CAR_LLM_DIR:-${RKSDK}/test_rkllm_run}"
VENV="${AI_CAR_LLM_VENV:-${RKSDK}/.venv/bin/activate}"
clear; echo "终端4 — aichat.py --yes  (1米≈1秒, 90度≈4秒)"
[[ ! -f "${LLM_DIR}/aichat.py" ]] && echo "[错误] 未找到 aichat.py" && exec bash
source "${VENV}"; cd "${LLM_DIR}"; exec python aichat.py --yes
T4
}

_gen_voice_scripts() {
  if [[ -f "${BOOT_DIR}/voice_to_ai_car.py" ]]; then
    cp -f "${BOOT_DIR}/voice_to_ai_car.py" "${TARGET}/voice_to_ai_car.py"
  else
cat > "${TARGET}/voice_to_ai_car.py" <<'PY'
#!/usr/bin/env python3
import importlib.util, os, struct, sys
try:
    import sherpa_onnx
except ImportError:
    print("[错误] 需要 sherpa_onnx", flush=True); sys.exit(1)
RKSDK = os.path.expanduser(os.environ.get("AI_CAR_RKSDK", "~/Desktop/ai_app/RKSDK"))
LLM_DIR = os.path.expanduser(os.environ.get("AI_CAR_LLM_DIR", f"{RKSDK}/test_rkllm_run"))
AICHAT = os.path.join(LLM_DIR, "aichat.py")
MODEL = os.path.expanduser(os.environ.get("SHERPA_MODEL",
    "~/Desktop/rk3588-offline-bundle/model/"
    "sherpa-onnx-rk3588-streaming-zipformer-small-bilingual-zh-en-2023-02-16"))
SR, CHUNK = 16000, 3200
def load_aichat():
    spec = importlib.util.spec_from_file_location("aichat", AICHAT)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
def txt(rec, st):
    r = rec.get_result(st)
    return (r if isinstance(r, str) else getattr(r, "text", str(r))).strip()
aichat = load_aichat()
host = os.environ.get("AI_CAR_LLM_HOST", "http://127.0.0.1:8001")
path = os.environ.get("AI_CAR_LLM_PATH", "/rkllm_chat")
car_cmd = os.path.expanduser(os.environ.get("CAR_CMD", aichat.resolve_default_car_cmd()))
print(f"语音→LLM: {host}{path}", flush=True)
rec = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=f"{MODEL}/tokens.txt", encoder=f"{MODEL}/encoder.rknn",
    decoder=f"{MODEL}/decoder.rknn", joiner=f"{MODEL}/joiner.rknn",
    provider=os.environ.get("SHERPA_PROVIDER", "rknn"), num_threads=1,
    sample_rate=SR, feature_dim=80, enable_endpoint_detection=True,
    rule1_min_trailing_silence=0.8, rule2_min_trailing_silence=0.5, rule3_min_utterance_length=0.2)
st = rec.create_stream(); last = ""; warmed = False
while True:
    chunk = sys.stdin.buffer.read(CHUNK)
    if not chunk: break
    samples = [s/32768.0 for s in struct.unpack("<" + "h"*(len(chunk)//2), chunk)]
    st.accept_waveform(SR, samples)
    while rec.is_ready(st): rec.decode_stream(st)
    p = txt(rec, st)
    if p and p != last: print(f"\r… {p}", end="", flush=True); last = p
    if rec.is_endpoint(st):
        f = txt(rec, st); print(flush=True)
        if f:
            print(f"[识别] {f}", flush=True)
            if any(k in f for k in ("停止","停下","别动","停车")) or f.strip()=="停":
                aichat.run_car_cmd(car_cmd, ["stop"], direct=False, idle_exit=300.0, rate_hz=20.0); warmed=True
            else:
                warmed = aichat.process_user_turn(f, host=host, path=path, car_cmd_path=car_cmd, yes=True, warmed=warmed)
        rec.reset(st); last = ""
PY
  fi
  echo '#!/usr/bin/env python3' > "${TARGET}/voice_to_llm.py"
  echo 'import runpy; runpy.run_path(__import__("pathlib").Path(__file__).with_name("voice_to_ai_car.py"))' >> "${TARGET}/voice_to_llm.py"
}

_gen_start_install_run() {
cat > "${TARGET}/install_ai_car.sh" <<'INS'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/ai_car_common.sh"
AI_CAR_ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS
DESKTOP="${UI_DESKTOP_DIR:-${HOME}/Desktop}"
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LAUNCHER="${DESKTOP}/NovaJoy-大模型控车.sh"

echo "==> 安装到 ${SCRIPT_DIR}"
chmod +x "${SCRIPT_DIR}"/*.sh 2>/dev/null || true
chmod +x "${SCRIPT_DIR}"/*.py 2>/dev/null || true
ai_car_copy_car_cmd "${RKSDK}" "${AI_CAR_ROS_WS}"

{
  echo '#!/usr/bin/env bash'
  echo 'export DISPLAY="${DISPLAY:-:0}"'
  echo "exec bash \"${SCRIPT_DIR}/run_ai_car_all.sh\""
} > "${LAUNCHER}"
chmod +x "${LAUNCHER}" 2>/dev/null || true

echo "安装完成。"
echo "  启动: bash ${SCRIPT_DIR}/start_ai_car.sh"
echo "  或:   bash ${SCRIPT_DIR}/run_ai_car_all.sh"
INS

cat > "${TARGET}/start_ai_car.sh" <<'STA'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/ai_car_common.sh"
AI_CAR_ROS_WS="$(ai_car_resolve_ros_ws "${SCRIPT_DIR}")"
export AI_CAR_ROS_WS
MICROROS_WS="${AI_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
LLM_DIR="${AI_CAR_LLM_DIR:-${RKSDK}/test_rkllm_run}"
SHERPA_VENV="${SHERPA_VENV:-${HOME}/Desktop/rk3588-offline-bundle/venv/bin/activate}"
export DISPLAY="${DISPLAY:-:0}"
export CAR_CMD="${CAR_CMD:-${AI_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"
echo "========================================"
echo "  NovaJoy 大模型控车 + 语音"
echo "========================================"
export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"
export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export AI_CAR_LLM_PATH="${AI_CAR_LLM_PATH:-/rkllm_chat}"
ai_car_start_stack "${SCRIPT_DIR}" "${MICROROS_WS}" "${AI_CAR_ROS_WS}" "${RKSDK}" "${LLM_DIR}"
ai_car_source_ros
if [[ -f "${CAR_CMD}" ]]; then bash "${CAR_CMD}" warmup 2>/dev/null || true; fi
if [[ ! -f "${SHERPA_VENV}" ]]; then echo "[错误] 无 Sherpa venv: ${SHERPA_VENV}"; exit 1; fi
ai_car_wait_llm || { echo "[错误] 请先等终端4 flask 启动"; exit 1; }
source "${SHERPA_VENV}"
AUDIO_DEV="$(ai_car_detect_astra_device_or_die)"
echo ""
echo "终端1: 语音 → 大模型控车"
echo "麦克风: ${AUDIO_DEV}"
echo "LLM: ${AI_CAR_LLM_HOST}${AI_CAR_LLM_PATH}"
ai_car_run_stt "${AUDIO_DEV}" "${SCRIPT_DIR}/voice_to_ai_car.py"
STA

cat > "${TARGET}/run_ai_car_all.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export DISPLAY="${DISPLAY:-:0}"
export AI_CAR_RKSDK="${AI_CAR_RKSDK:-${HOME}/Desktop/ai_app/RKSDK}"
export AI_CAR_LLM_DIR="${AI_CAR_LLM_DIR:-${AI_CAR_RKSDK}/test_rkllm_run}"
export AI_CAR_MICROROS_WS="${AI_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
export AI_CAR_ROS_WS="${AI_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}"
export CAR_CMD="${CAR_CMD:-${AI_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"
export AI_CAR_LLM_HOST="${AI_CAR_LLM_HOST:-http://127.0.0.1:8001}"
export AI_CAR_LLM_PATH="${AI_CAR_LLM_PATH:-/rkllm_chat}"
export AI_CAR_SUDO_PASS="${AI_CAR_SUDO_PASS:-rock}"
echo "========================================"
echo "  NovaJoy 大模型控车 — 一键总启动"
echo "  终端1=语音 2=底盘 3=MicroROS 4=flask"
echo "========================================"
bash "${SCRIPT_DIR}/install_ai_car.sh"
if [[ ! -f "${SCRIPT_DIR}/start_ai_car.sh" ]]; then
  echo "[错误] 缺少 start_ai_car.sh，请重新运行 bootstrap_ai_car_all.sh"
  exit 1
fi
echo ""
echo "[2/2] 启动…"
exec bash "${SCRIPT_DIR}/start_ai_car.sh"
RUN
}

# ---------- 生成所有文件 ----------
FILES=(
  ai_car_common.sh ai_car_sudo_askpass.sh ai_car_microros_term.sh ai_car_chassis_term.sh
  ai_car_llm_server_term.sh ai_car_voice_term.sh ai_car_chat_term.sh
  voice_to_ai_car.py voice_to_llm.py install_ai_car.sh start_ai_car.sh run_ai_car_all.sh
)

COPIED=0
for f in "${FILES[@]}"; do
  if _copy_if_exists "${f}"; then
    echo -e "  ${CYAN}复制${NC} ${f}"
    COPIED=1
  fi
done

if [[ "${COPIED}" -eq 0 ]] || [[ ! -f "${TARGET}/start_ai_car.sh" ]]; then
  echo -e "${YELLOW}内嵌生成全部文件…${NC}"
  _gen_ai_car_common
  _gen_term_scripts
  _gen_voice_scripts
  _gen_start_install_run
fi

chmod +x "${TARGET}"/*.sh 2>/dev/null || true
chmod +x "${TARGET}"/*.py 2>/dev/null || true

# 复制本 bootstrap 到目标目录备查
cp -f "${BOOT_DIR}/$(basename "$0")" "${TARGET}/bootstrap_ai_car_all.sh" 2>/dev/null || true

# 桌面快捷方式（指向 bootstrap，方便只拷一个文件的用户）
DESKTOP="${UI_DESKTOP_DIR:-${HOME}/Desktop}"
cat > "${DESKTOP}/NovaJoy-大模型控车.sh" <<LAUNCH
#!/usr/bin/env bash
export DISPLAY="\${DISPLAY:-:0}"
exec bash "${TARGET}/run_ai_car_all.sh"
LAUNCH
chmod +x "${DESKTOP}/NovaJoy-大模型控车.sh"

echo ""
echo -e "${GREEN}已生成到 ${TARGET}:${NC}"
ls -1 "${TARGET}"/ai_car_* "${TARGET}"/voice_to_ai_car.py "${TARGET}"/install_ai_car.sh "${TARGET}"/start_ai_car.sh "${TARGET}"/run_ai_car_all.sh 2>/dev/null | sed 's/^/  /'
echo ""
echo -e "桌面快捷: ${DESKTOP}/NovaJoy-大模型控车.sh"
echo -e "下次启动: ${YELLOW}bash ${TARGET}/run_ai_car_all.sh${NC}"

if [[ "${GEN_ONLY}" -eq 1 ]]; then
  echo -e "${GREEN}(--gen-only 模式，未启动)${NC}"
  exit 0
fi

echo ""
echo -e "${YELLOW}即将启动…${NC}"
exec bash "${TARGET}/run_ai_car_all.sh"
