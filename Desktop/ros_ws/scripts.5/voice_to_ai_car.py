#!/usr/bin/env python3
"""
Astra + Sherpa → aichat 同款 LLM 控车（兼容旧版 aichat，无 process_user_turn 也可用）
"""
import importlib.util
import os
import struct
import sys

try:
    import sherpa_onnx
except ImportError:
    print("[错误] 需要 sherpa_onnx，请先 source rk3588-offline-bundle/venv", flush=True)
    sys.exit(1)

RKSDK = os.path.expanduser(os.environ.get("AI_CAR_RKSDK", "~/Desktop/ai_app/RKSDK"))
LLM_DIR = os.path.expanduser(os.environ.get("AI_CAR_LLM_DIR", f"{RKSDK}/test_rkllm_run"))
AICHAT_PATH = os.path.join(LLM_DIR, "aichat.py")

MODEL = os.environ.get(
    "SHERPA_MODEL",
    os.path.expanduser(
        "~/Desktop/rk3588-offline-bundle/model/"
        "sherpa-onnx-rk3588-streaming-zipformer-small-bilingual-zh-en-2023-02-16"
    ),
)
SR, CHUNK = 16000, 3200
_LLM_DOWN_PRINTED = False


def llm_reachable(host: str, timeout: float = 2.0) -> bool:
    import urllib.error
    import urllib.request

    base = host.rstrip("/")
    for url in (f"{base}/", f"{base}/rkllm_chat"):
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            continue
    return False


def should_send_to_llm(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 3:
        return False
    if any(k in t for k in ("停止", "停下", "别动", "停车")) or t == "停":
        return True
    cmd_keys = ("前进", "后退", "左转", "右转", "米", "度", "走", "开", "转", "移", "动")
    if any(k in t for k in cmd_keys):
        return True
    return len(t) >= 5


def print_llm_down_once(host: str) -> None:
    global _LLM_DOWN_PRINTED
    if _LLM_DOWN_PRINTED:
        return
    _LLM_DOWN_PRINTED = True
    print(
        "[错误] 大模型未启动 (Connection refused)。\n"
        "  请先开终端4:\n"
        "    cd ~/Desktop/ai_app/RKSDK/test_rkllm_run\n"
        "    source ~/Desktop/ai_app/RKSDK/.venv/bin/activate\n"
        "    python3 flask_server.py\n"
        f"  等到 Running on :8001 后再说话。检查: curl -s {host}/",
        flush=True,
    )


def load_aichat():
    if not os.path.isfile(AICHAT_PATH):
        print(f"[错误] 未找到 {AICHAT_PATH}", flush=True)
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("aichat", AICHAT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def txt(rec, st):
    r = rec.get_result(st)
    return (r if isinstance(r, str) else getattr(r, "text", str(r))).strip()


def process_turn(ac, user_input, host, path, car_cmd, warmed):
    """调用 aichat.process_user_turn；旧版则走底层函数。"""
    sec_per_meter = float(os.environ.get("AI_CAR_SEC_PER_METER", "1.0"))
    sec_per_turn90 = float(os.environ.get("AI_CAR_SEC_PER_TURN90", "4.0"))

    if hasattr(ac, "process_user_turn"):
        return ac.process_user_turn(
            user_input,
            host=host,
            path=path,
            car_cmd_path=car_cmd,
            yes=True,
            no_warmup=False,
            warmed=warmed,
            sec_per_meter=sec_per_meter,
            sec_per_turn90=sec_per_turn90,
        )

    # 旧版 aichat 兼容
    user_input = (user_input or "").strip()
    if not user_input:
        return warmed

    content = f"{ac.CONTROL_PROMPT}\n用户指令：\n{user_input}\n"
    payload = {
        "model": os.environ.get("AI_CAR_MODEL", "Octopus-v2"),
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }
    url = ac.build_url(host, path)
    if not llm_reachable(host):
        print_llm_down_once(host)
        return warmed
    try:
        response = ac.post_json(url, payload, timeout_sec=60.0)
    except Exception as exc:
        err = str(exc)
        if "111" in err or "Connection refused" in err or "refused" in err.lower():
            print_llm_down_once(host)
        else:
            print(f"Error: {exc}", file=sys.stderr, flush=True)
        return warmed

    try:
        reply = ac.extract_reply(response).strip()
    except Exception:
        print(f"Unexpected response: {response}", file=sys.stderr, flush=True)
        return warmed

    print(f"Assistant: {reply}", flush=True)
    if reply == "小车无法完成任务。":
        return warmed

    rtt_end = os.environ.get("AI_CAR_RTT_END", "<rtt_end>")
    exec_text = ac.select_exec_text(reply, rtt_end)
    calls = ac.dedupe_duplicate_calls(ac.parse_calls(exec_text))
    if not calls:
        return warmed

    plan = []
    for fn_name, call_args in calls[:8]:
        try:
            action, speed, duration = ac.call_to_car_cmd(
                fn_name, call_args,
                default_linear=0.5, default_angular=0.5,
                max_linear=1.0, max_angular=1.0, max_duration=5.0,
                sec_per_meter=sec_per_meter, sec_per_turn90=sec_per_turn90,
                turn_unit_deg=90.0, turn_unit_threshold=10.0,
            )
        except Exception as exc:
            print(f"Skip {fn_name}({call_args}): {exc}", file=sys.stderr, flush=True)
            continue
        plan.append((action, speed, duration))

    if not plan:
        return warmed

    print("执行计划:", flush=True)
    for i, (action, speed, duration) in enumerate(plan, 1):
        label = {"fwd": "前进", "back": "后退", "ccw": "左转", "cw": "右转"}.get(action, action)
        print(f"  {i}. {label} speed={speed:.3f} duration={duration:.2f}s", flush=True)

    if not os.path.exists(car_cmd):
        print(f"WARN: 无 car_cmd: {car_cmd}", file=sys.stderr, flush=True)
        return warmed

    if not warmed:
        warmed = ac.warmup_car(car_cmd, direct=False, idle_exit=300.0, rate_hz=20.0)

    for action, speed, duration in plan:
        ac.run_car_cmd(
            car_cmd, [action, str(speed), str(duration)],
            direct=False, idle_exit=300.0, rate_hz=20.0,
        )
    return warmed


def main():
    aichat = load_aichat()
    host = os.environ.get("AI_CAR_LLM_HOST", "http://127.0.0.1:8001")
    path = os.environ.get("AI_CAR_LLM_PATH", "/rkllm_chat")
    car_cmd = os.path.expanduser(os.environ.get("CAR_CMD", aichat.resolve_default_car_cmd()))

    print("语音 → 大模型控车（aichat 链路）", flush=True)
    print(f"  LLM: {host}{path}", flush=True)
    print(f"  car_cmd: {car_cmd}", flush=True)
    print(f"  aichat: {AICHAT_PATH}", flush=True)
    if not hasattr(aichat, "process_user_turn"):
        print("  [提示] 旧版 aichat，已启用兼容模式", flush=True)
    print("  说完一句后停顿约 1 秒", flush=True)

    if not llm_reachable(host):
        print_llm_down_once(host)
        sys.exit(1)
    print("  [OK] 大模型服务已连通", flush=True)

    rec = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f"{MODEL}/tokens.txt",
        encoder=f"{MODEL}/encoder.rknn",
        decoder=f"{MODEL}/decoder.rknn",
        joiner=f"{MODEL}/joiner.rknn",
        provider=os.environ.get("SHERPA_PROVIDER", "rknn"),
        num_threads=1,
        sample_rate=SR,
        feature_dim=80,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=float(os.environ.get("VOICE_EP_RULE1", "0.8")),
        rule2_min_trailing_silence=float(os.environ.get("VOICE_EP_RULE2", "0.5")),
        rule3_min_utterance_length=float(os.environ.get("VOICE_EP_RULE3", "0.2")),
    )
    st = rec.create_stream()
    last = ""
    warmed = False

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
                print(f"[识别] {f}", flush=True)
                if any(k in f for k in ("停止", "停下", "别动", "停车")) or f.strip() == "停":
                    aichat.run_car_cmd(car_cmd, ["stop"], direct=False, idle_exit=300.0, rate_hz=20.0)
                    warmed = True
                elif should_send_to_llm(f):
                    warmed = process_turn(aichat, f, host, path, car_cmd, warmed)
                else:
                    print("  [跳过] 太短或非控车指令，请说：前进一米 / 后退 / 左转", flush=True)
            rec.reset(st)
            last = ""


if __name__ == "__main__":
    main()
