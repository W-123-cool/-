#!/usr/bin/env bash
# 一键安装语音控车：生成 Python 脚本 + 启动器 + 桌面快捷方式
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

voice_car_common_needs_refresh() {
  [[ ! -f "${SCRIPT_DIR}/voice_car_common.sh" ]] && return 0
  grep -q 'voice_car_resolve_ros_ws' "${SCRIPT_DIR}/voice_car_common.sh" 2>/dev/null || return 0
  grep -q 'voice_car_start_chassis_stack' "${SCRIPT_DIR}/voice_car_common.sh" 2>/dev/null || return 0
  return 1
}

# 旧版 common 自动升级
if voice_car_common_needs_refresh; then
  cat > "${SCRIPT_DIR}/voice_car_common.sh" <<'COMMON'
#!/usr/bin/env bash
voice_car_resolve_ros_ws() {
  local script_dir="${1:-}"
  local candidate
  if [[ -n "${VOICE_CAR_ROS_WS:-}" && -f "${VOICE_CAR_ROS_WS}/car_cmd.sh" ]]; then
    echo "${VOICE_CAR_ROS_WS}"; return 0
  fi
  for candidate in "${HOME}/Desktop/rock_ws/ros_ws" "${script_dir}/.." "${HOME}/rock_ws/ros_ws"; do
    if [[ -n "${candidate}" && -f "${candidate}/car_cmd.sh" ]]; then
      echo "$(cd "${candidate}" && pwd)"; return 0
    fi
  done
  echo "${HOME}/Desktop/rock_ws/ros_ws"
}
voice_car_detect_astra_card() {
  arecord -l 2>/dev/null | grep -iE 'astra|orbbec' | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1
}
voice_car_detect_pulse_source() {
  command -v pactl >/dev/null 2>&1 || return 1
  pactl list sources short 2>/dev/null | awk '/[Oo]rbbec|[Aa]stra/ && !/monitor/ {print $2; exit}'
}
voice_car_detect_astra_device_or_die() {
  local card pulse_src
  card="$(voice_car_detect_astra_card)"
  if [[ -n "${card}" ]]; then echo "alsa:plughw:${card},0"; return 0; fi
  pulse_src="$(voice_car_detect_pulse_source)"
  if [[ -n "${pulse_src}" ]]; then echo "pulse:${pulse_src}"; return 0; fi
  echo "[错误] 未找到 Astra 麦克风。请确认 lsusb 有 2bc5:0403 后重试。" >&2
  lsusb | grep -i 2bc5 || true; arecord -l || true
  pactl list sources short 2>/dev/null | grep -i orbbec || true
  exit 1
}
voice_car_detect_astra_card_or_die() { voice_car_detect_astra_device_or_die; }
voice_car_run_stt() {
  local device="$1" py_script="$2"
  case "${device}" in
    alsa:*) exec arecord -D "${device#alsa:}" -f S16_LE -r 16000 -c 1 -t raw | python3 "${py_script}" ;;
    pulse:*)
      command -v parecord >/dev/null 2>&1 || { echo "[错误] 需要 parecord" >&2; exit 1; }
      exec parecord --device="${device#pulse:}" --format=s16le --rate=16000 --channels=1 --raw | python3 "${py_script}"
      ;;
    *) echo "[错误] 未知录音设备: ${device}" >&2; exit 1 ;;
  esac
}
voice_car_open_terminal() {
  local title="$1" inner_cmd="$2"
  export DISPLAY="${DISPLAY:-:0}"
  if command -v gnome-terminal >/dev/null 2>&1; then gnome-terminal --title="${title}" -- bash -lc "${inner_cmd}; exec bash"; return 0; fi
  if command -v xfce4-terminal >/dev/null 2>&1; then xfce4-terminal --title="${title}" -e "bash -lc '${inner_cmd}; exec bash'"; return 0; fi
  if command -v lxterminal >/dev/null 2>&1; then lxterminal --title="${title}" -e "bash -lc '${inner_cmd}; exec bash'"; return 0; fi
  if command -v xterm >/dev/null 2>&1; then xterm -title "${title}" -e bash -lc "${inner_cmd}; exec bash" & return 0; fi
  echo ""; echo "[提示] 未能自动开新终端，请手动新开终端执行："; echo "  ${inner_cmd}"; echo ""; return 1
}
voice_car_microros_running() { pgrep -f "micro_ros_agent.*8888" >/dev/null 2>&1; }
voice_car_prepare_serial() {
  local ros_ws="$1" serial="${VOICE_CAR_SERIAL:-/dev/rt_shell}" setup="${ros_ws}/usb_auto_setup.sh"
  if [[ -e "${serial}" ]]; then echo "  串口已就绪: ${serial}"; return 0; fi
  echo "  未找到 ${serial}，正在运行 usb_auto_setup.sh …"
  [[ -f "${setup}" ]] || { echo "[警告] 未找到 ${setup}" >&2; return 1; }
  ( cd "${ros_ws}" && bash "${setup}" ) || return 1
  [[ -e "${serial}" ]] && { echo "  串口已创建: ${serial}"; return 0; }
  echo "[警告] usb_auto_setup 完成但仍无 ${serial}" >&2; return 1
}
voice_car_start_chassis_stack() {
  local script_dir="$1" microros_ws="$2" ros_ws="$3" rock_ip
  rock_ip="$(hostname -I | awk '{print $1}')"
  export VOICE_CAR_MICROROS_WS="${microros_ws}" VOICE_CAR_ROS_WS="${ros_ws}" VOICE_CAR_ROCK_IP="${rock_ip}"
  echo ""; echo "==== 底盘启动（自动开终端）===="
  voice_car_prepare_serial "${ros_ws}" || true
  echo ""; echo "[终端1] MicroROS Agent"
  if voice_car_microros_running; then echo "  → MicroROS 已在运行，跳过"
  else voice_car_open_terminal "终端1-MicroROS" "export VOICE_CAR_MICROROS_WS='${microros_ws}'; bash '${script_dir}/voice_car_microros_term.sh'"
    echo "  → 已新开终端，等待 Agent 启动 (3s)…"; sleep 3; fi
  echo ""; echo "[终端2] 底盘 minicom"
  voice_car_open_terminal "终端2-底盘minicom" "export VOICE_CAR_ROS_WS='${ros_ws}'; export VOICE_CAR_ROCK_IP='${rock_ip}'; bash '${script_dir}/voice_car_chassis_term.sh'"
  echo ""; echo "----------------------------------------"
  echo "  请在「终端2-底盘minicom」完成 RT-Thread 命令后回到本窗口继续"
  echo "----------------------------------------"
}
voice_car_source_ros() {
  set +u
  source /opt/ros/foxy/setup.bash
  if [[ -f "${VOICE_CAR_ROS_WS}/install/setup.bash" ]]; then source "${VOICE_CAR_ROS_WS}/install/setup.bash"; fi
  set -u
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
}
COMMON
  chmod +x "${SCRIPT_DIR}/voice_car_common.sh"
fi

# shellcheck source=voice_car_common.sh
source "${SCRIPT_DIR}/voice_car_common.sh"
if ! declare -F voice_car_resolve_ros_ws >/dev/null 2>&1; then
  echo "[错误] voice_car_common.sh 不完整，正在重写…" >&2
  rm -f "${SCRIPT_DIR}/voice_car_common.sh"
  exec bash "$0"
fi
VOICE_CAR_ROS_WS="$(voice_car_resolve_ros_ws "${SCRIPT_DIR}")"
export VOICE_CAR_ROS_WS

SHERPA_BUNDLE="${SHERPA_BUNDLE:-${HOME}/Desktop/rk3588-offline-bundle}"
DESKTOP="${UI_DESKTOP_DIR:-${HOME}/Desktop}"

echo "==> 安装语音控车到 ${SCRIPT_DIR}"
echo "    ROS 工作区: ${VOICE_CAR_ROS_WS}"

# --- astra_sherpa_stream.py ---
cat > "${SCRIPT_DIR}/astra_sherpa_stream.py" <<'PY'
#!/usr/bin/env python3
"""Astra 麦克风 → Sherpa 流式识别（仅文字）"""
import os, struct, sys, sherpa_onnx

MODEL = os.environ.get(
    "SHERPA_MODEL",
    os.path.expanduser("~/Desktop/rk3588-offline-bundle/model/"
                       "sherpa-onnx-rk3588-streaming-zipformer-small-bilingual-zh-en-2023-02-16"),
)
SR, CHUNK = 16000, 3200

def txt(rec, st):
    r = rec.get_result(st)
    return (r if isinstance(r, str) else getattr(r, "text", str(r))).strip()

rec = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=f"{MODEL}/tokens.txt", encoder=f"{MODEL}/encoder.rknn",
    decoder=f"{MODEL}/decoder.rknn", joiner=f"{MODEL}/joiner.rknn",
    provider=os.environ.get("SHERPA_PROVIDER", "rknn"), num_threads=1,
    sample_rate=SR, feature_dim=80,
    enable_endpoint_detection=True,
    rule1_min_trailing_silence=float(os.environ.get("VOICE_EP_RULE1", "0.8")),
    rule2_min_trailing_silence=float(os.environ.get("VOICE_EP_RULE2", "0.5")),
    rule3_min_utterance_length=float(os.environ.get("VOICE_EP_RULE3", "0.2")),
)
st = rec.create_stream()
last = ""
print("Listening... Ctrl+C stop", flush=True)
while True:
    chunk = sys.stdin.buffer.read(CHUNK)
    if not chunk:
        break
    n = len(chunk) // 2
    samples = [s / 32768.0 for s in struct.unpack("<" + "h" * n, chunk)]
    st.accept_waveform(SR, samples)
    while rec.is_ready(st):
        rec.decode_stream(st)
    p = txt(rec, st)
    if p and p != last:
        print(f"\r… {p}", end="", flush=True)
        last = p
    if rec.is_endpoint(st):
        f = txt(rec, st)
        print(flush=True)
        if f:
            print(f, flush=True)
        rec.reset(st)
        last = ""
PY

# --- astra_voice_car.py ---
cat > "${SCRIPT_DIR}/astra_voice_car.py" <<'PY'
#!/usr/bin/env python3
"""Astra + Sherpa + car_cmd 语音控车"""
import os, re, struct, subprocess, sys, sherpa_onnx

MODEL = os.environ.get(
    "SHERPA_MODEL",
    os.path.expanduser("~/Desktop/rk3588-offline-bundle/model/"
                       "sherpa-onnx-rk3588-streaming-zipformer-small-bilingual-zh-en-2023-02-16"),
)
CAR_CMD = os.environ.get(
    "CAR_CMD", os.path.expanduser("~/Desktop/rock_ws/ros_ws/car_cmd.sh"))
AI_HOOK = os.environ.get("AI_APP_VOICE_HOOK", "").strip()
SPD = float(os.environ.get("VOICE_CAR_SPEED", "0.15"))
TURN = float(os.environ.get("VOICE_CAR_TURN", "0.30"))
SR, CHUNK = 16000, 3200
CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
      "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}

def txt(rec, st):
    r = rec.get_result(st)
    return (r if isinstance(r, str) else getattr(r, "text", str(r))).strip()

def meters(t):
    m = re.search(r"(\d+(?:\.\d+)?|" + "|".join(CN.keys()) + r")+\s*米", t)
    if not m:
        return 1.0
    s = m.group(1)
    if re.match(r"^\d", s):
        return float(s)
    if s == "半":
        return 0.5
    if s == "十":
        return 10.0
    return float(CN.get(s, 1))

def parse_one(t):
    t = re.sub(r"\s+", "", t)
    if not t:
        return None
    if any(k in t for k in ("停止", "停下", "别动")) or t in ("停",):
        return ("stop",)
    d = max(0.3, meters(t) / SPD)
    if any(k in t for k in ("后退", "倒车", "向后")):
        return ("back", SPD, d)
    if any(k in t for k in ("左转", "向左转", "左拐")):
        return ("ccw", TURN, max(0.5, d if "米" in t else 1.0))
    if any(k in t for k in ("右转", "向右转", "右拐")):
        return ("cw", TURN, max(0.5, d if "米" in t else 1.0))
    if any(k in t for k in ("左移", "向左")):
        return ("left", SPD, d)
    if any(k in t for k in ("右移", "向右")):
        return ("right", SPD, d)
    if any(k in t for k in ("前进", "向前", "直走", "走")):
        return ("fwd", SPD, d)
    return None

def parse(t):
    t = re.sub(r"\s+", "", t)
    if not t:
        return None
    parts = re.split(r"[，,。！？；;]|然后|以后|再来|之后", t)
    for part in parts:
        p = parse_one(part)
        if p:
            return p
    return parse_one(t)

def run(args):
    env = os.environ.copy()
    env["CAR_CMD_PYTHON"] = os.environ.get("CAR_CMD_PYTHON", "/usr/bin/python3")
    cmd = ["bash", CAR_CMD] + [str(x) for x in args]
    print(f"[执行] {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, env=env, check=False)

def on_final(text):
    print(f"[识别] {text}", flush=True)
    if AI_HOOK:
        subprocess.run([AI_HOOK, text], check=False)
        return
    p = parse(text)
    if not p:
        print("[跳过] 可说：前进一米 / 后退 / 左转 / 停止", flush=True)
        return
    run(p)

rec = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=f"{MODEL}/tokens.txt", encoder=f"{MODEL}/encoder.rknn",
    decoder=f"{MODEL}/decoder.rknn", joiner=f"{MODEL}/joiner.rknn",
    provider=os.environ.get("SHERPA_PROVIDER", "rknn"), num_threads=1,
    sample_rate=SR, feature_dim=80,
    enable_endpoint_detection=True,
    rule1_min_trailing_silence=float(os.environ.get("VOICE_EP_RULE1", "0.8")),
    rule2_min_trailing_silence=float(os.environ.get("VOICE_EP_RULE2", "0.5")),
    rule3_min_utterance_length=float(os.environ.get("VOICE_EP_RULE3", "0.2")),
)
st = rec.create_stream()
last = ""
print("语音控车：说完一句后停顿约 1 秒再执行", flush=True)
while True:
    chunk = sys.stdin.buffer.read(CHUNK)
    if not chunk:
        break
    n = len(chunk) // 2
    samples = [s / 32768.0 for s in struct.unpack("<" + "h" * n, chunk)]
    st.accept_waveform(SR, samples)
    while rec.is_ready(st):
        rec.decode_stream(st)
    p = txt(rec, st)
    if p and p != last:
        print(f"\r… {p}", end="", flush=True)
        last = p
    if rec.is_endpoint(st):
        f = txt(rec, st)
        print(flush=True)
        if f:
            on_final(f)
        rec.reset(st)
        last = ""
PY

# --- 底盘终端引导 ---
cat > "${SCRIPT_DIR}/voice_car_chassis_term.sh" <<'CHASSIS'
#!/usr/bin/env bash
ROCK_IP="${VOICE_CAR_ROCK_IP:-$(hostname -I | awk '{print $1}')}"
ROS_WS="${VOICE_CAR_ROS_WS:-${HOME}/Desktop/rock_ws/ros_ws}"
SERIAL="${VOICE_CAR_SERIAL:-/dev/rt_shell}"
BAUD="${VOICE_CAR_BAUD:-1500000}"
USB_SETUP="${ROS_WS}/usb_auto_setup.sh"

clear
echo "=============================================="
echo "  终端2 — 底盘串口 (minicom)"
echo "=============================================="
echo ""
echo "步骤："
echo "  1) 若 /dev/rt_shell 不存在，先运行 usb_auto_setup.sh"
echo "  2) 启动 minicom"
echo "  3) 进入 RT-Thread shell 后依次输入："
echo ""
echo "     microros_chassis udp ${ROCK_IP} 8888"
echo "     chassis_car_app"
echo ""
echo "  4) 完成后回到「语音控车」窗口按 Enter"
echo "=============================================="
echo ""

if [[ ! -e "${SERIAL}" ]]; then
  if [[ -f "${USB_SETUP}" ]]; then
    echo ">>> 正在执行: bash ${USB_SETUP}"
    ( cd "${ROS_WS}" && bash "${USB_SETUP}" ) || true
  else
    echo "[警告] 未找到 ${USB_SETUP}"
  fi
fi

if [[ ! -e "${SERIAL}" ]]; then
  echo "[错误] 串口仍不存在: ${SERIAL}"
  echo "请检查底盘 USB 连接后重试"
  exec bash
fi

echo ">>> 即将启动: minicom -D ${SERIAL} -b ${BAUD}"
echo ""
read -r -p "按 Enter 进入 minicom…" _
exec minicom -D "${SERIAL}" -b "${BAUD}"
CHASSIS

# --- MicroROS 终端 ---
cat > "${SCRIPT_DIR}/voice_car_microros_term.sh" <<'MICRO'
#!/usr/bin/env bash
MICROROS_WS="${VOICE_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"

clear
echo "=============================================="
echo "  终端1 — MicroROS Agent"
echo "=============================================="
echo ""
echo "本窗口将自动执行："
echo "  cd ${MICROROS_WS}"
echo "  source install/setup.bash"
echo "  ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888"
echo ""
echo "  保持本窗口运行，不要关闭"
echo "=============================================="
echo ""

if [[ ! -d "${MICROROS_WS}/install" ]]; then
  echo "[错误] 未找到 ${MICROROS_WS}/install"
  exec bash
fi

set +u
source /opt/ros/foxy/setup.bash
source "${MICROROS_WS}/install/setup.bash"
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

cd "${MICROROS_WS}"
exec ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
MICRO

chmod +x "${SCRIPT_DIR}/astra_sherpa_stream.py"
chmod +x "${SCRIPT_DIR}/astra_voice_car.py"
chmod +x "${SCRIPT_DIR}/voice_car_chassis_term.sh"
chmod +x "${SCRIPT_DIR}/voice_car_microros_term.sh"
chmod +x "${SCRIPT_DIR}/voice_car_common.sh"
chmod +x "${SCRIPT_DIR}/install_voice_car.sh"

# 启动脚本：旧版自动升级
if [[ ! -f "${SCRIPT_DIR}/start_voice_car.sh" ]] || \
   ! grep -q 'voice_car_start_chassis_stack' "${SCRIPT_DIR}/start_voice_car.sh" 2>/dev/null; then
  cat > "${SCRIPT_DIR}/start_voice_car.sh" <<'START'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/voice_car_common.sh"
VOICE_CAR_ROS_WS="$(voice_car_resolve_ros_ws "${SCRIPT_DIR}")"
export VOICE_CAR_ROS_WS
MICROROS_WS="${VOICE_CAR_MICROROS_WS:-${HOME}/Desktop/rock_ws/microros_ws}"
SHERPA_BUNDLE="${SHERPA_BUNDLE:-${HOME}/Desktop/rk3588-offline-bundle}"
export VOICE_CAR_MICROROS_WS="${MICROROS_WS}"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
if [[ ! -f "${SCRIPT_DIR}/astra_voice_car.py" ]]; then
  echo -e "${YELLOW}首次运行，正在安装文件…${NC}"
  bash "${SCRIPT_DIR}/install_voice_car.sh"
fi
export DISPLAY="${DISPLAY:-:0}"
export CAR_CMD="${CAR_CMD:-${VOICE_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  NovaJoy 语音控车（一键启动）${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${CYAN}ROS 工作区: ${VOICE_CAR_ROS_WS}${NC}"
echo -e "${CYAN}car_cmd:    ${CAR_CMD}${NC}"
if [[ ! -f "${CAR_CMD}" ]]; then
  echo -e "${RED}[错误] 未找到 car_cmd.sh: ${CAR_CMD}${NC}"; exit 1
fi
VENV="${SHERPA_BUNDLE}/venv/bin/activate"
if [[ ! -f "${VENV}" ]]; then echo -e "${RED}[错误] 未找到 ${VENV}${NC}"; exit 1; fi
source "${VENV}"
AUDIO_DEV="$(voice_car_detect_astra_device_or_die)"
export AUDIO_DEV
echo -e "${CYAN}麦克风: ${AUDIO_DEV}${NC}"
voice_car_start_chassis_stack "${SCRIPT_DIR}" "${MICROROS_WS}" "${VOICE_CAR_ROS_WS}"
read -r -p "底盘 RT-Thread 命令完成后，按 Enter 继续语音控车…"
voice_car_source_ros
bash "${CAR_CMD}" warmup 2>/dev/null || true
read -r -p "是否先测试前进 2 秒? [y/N] " test_move
if [[ "${test_move,,}" == "y" || "${test_move,,}" == "yes" ]]; then
  bash "${CAR_CMD}" fwd 0.15 2 || true
  bash "${CAR_CMD}" stop || true
fi
echo -e "${GREEN}语音控车已启动。说完一句后停顿约 1 秒，例如：前进一米 / 停止${NC}"
voice_car_run_stt "${AUDIO_DEV}" "${SCRIPT_DIR}/astra_voice_car.py"
START
fi
chmod +x "${SCRIPT_DIR}/start_voice_car.sh"

# 若从完整 scripts 目录安装，用仓库内脚本覆盖（保证最新）
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
for _f in voice_car_common.sh start_voice_car.sh voice_car_chassis_term.sh voice_car_microros_term.sh one_shot_voice_car.sh run_voice_car_all.sh; do
  src="${PKG_DIR}/${_f}"
  if [[ -f "${src}" ]] && [[ "${src}" -ef "${SCRIPT_DIR}/${_f}" ]] 2>/dev/null; then
    continue
  fi
  if [[ -f "${src}" ]] && grep -q 'voice_car' "${src}" 2>/dev/null; then
    cp -f "${src}" "${SCRIPT_DIR}/${_f}"
    chmod +x "${SCRIPT_DIR}/${_f}"
  fi
done
unset _f PKG_DIR src

if [[ ! -f "${SCRIPT_DIR}/one_shot_voice_car.sh" ]]; then
  cat > "${SCRIPT_DIR}/one_shot_voice_car.sh" <<'ONESHOT'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "${DIR}/run_voice_car_all.sh"
ONESHOT
  chmod +x "${SCRIPT_DIR}/one_shot_voice_car.sh"
fi

if [[ ! -f "${SCRIPT_DIR}/run_voice_car_all.sh" ]]; then
  cat > "${SCRIPT_DIR}/run_voice_car_all.sh" <<'ALLIN'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export CAR_CMD="${CAR_CMD:-${HOME}/Desktop/rock_ws/ros_ws/car_cmd.sh}"
export CAR_CMD_PYTHON="${CAR_CMD_PYTHON:-/usr/bin/python3}"
bash "${DIR}/install_voice_car.sh"
exec bash "${DIR}/start_voice_car.sh"
ALLIN
  chmod +x "${SCRIPT_DIR}/run_voice_car_all.sh"
fi

# 桌面快捷方式
cat > "${DESKTOP}/NovaJoy-语音控车.sh" <<DESK
#!/usr/bin/env bash
export DISPLAY="\${DISPLAY:-:0}"
export CAR_CMD="\${CAR_CMD:-${VOICE_CAR_ROS_WS}/car_cmd.sh}"
export CAR_CMD_PYTHON="\${CAR_CMD_PYTHON:-/usr/bin/python3}"
exec bash "${SCRIPT_DIR}/run_voice_car_all.sh"
DESK
chmod +x "${DESKTOP}/NovaJoy-语音控车.sh"

echo ""
echo "安装完成。"
echo "  一键总启动: bash ${SCRIPT_DIR}/run_voice_car_all.sh"
echo "  兼容旧名:   bash ${SCRIPT_DIR}/one_shot_voice_car.sh"
echo "  或双击:     ${DESKTOP}/NovaJoy-语音控车.sh"
echo ""
echo "Sherpa venv: ${SHERPA_BUNDLE}/venv"
if [[ ! -f "${SHERPA_BUNDLE}/venv/bin/activate" ]]; then
  echo "[警告] 未找到 Sherpa venv，请先配置 rk3588-offline-bundle"
fi
