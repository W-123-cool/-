#!/usr/bin/env python3
"""
Voice tour / nav / QA — push-to-talk via double Enter.

Flow:
  1. Boot: KWS only until first wake word
  2. After wake: KWS off; Enter -> start mic; speak; Enter -> STT -> LLM
  3. During mission: KWS on for wake-word stop only
  4. Say goodbye: back to KWS boot
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROS_WS, "scripts")
if ROS_WS not in sys.path:
    sys.path.insert(0, ROS_WS)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

try:
    import sherpa_onnx
except ImportError:
    print("[error] need sherpa_onnx; source rk3588-offline-bundle/venv", flush=True)
    sys.exit(1)

from voice_nav.agent import VoiceNavAgent
from voice_nav.audio_capture import OnDemandAudioCapture, drain_capture
from voice_nav.input_trigger import (
    UiPushToTalkTrigger,
    make_input_trigger,
    publish_ptt_awake_sync,
    publish_ptt_final,
    publish_ptt_partial,
    publish_ptt_sleep,
    ui_input_mode,
)
from voice_nav.env_util import bootstrap_voice_nav_env, norm_env, normalize_voice_nav_env
from voice_nav.startup_check import apply_startup, announce_startup, check_startup
from voice_nav.stt_filter import normalize_spoken_text
from voice_nav import tts, wake
from voice_nav.audio_preprocess import denoise_enabled, get_preprocessor, preprocess_samples
from voice_nav.wake import wake_reply

MODEL = os.environ.get(
    "SHERPA_MODEL",
    os.path.expanduser(
        "~/Desktop/rk3588-offline-bundle/model/"
        "sherpa-onnx-rk3588-streaming-zipformer-small-bilingual-zh-en-2023-02-16"
    ),
)
SR = 16000

STATE_KWS_BOOT = "kws_boot"
STATE_IDLE = "idle"


def _make_recognizer() -> sherpa_onnx.OnlineRecognizer:
    # Endpoint off: user ends recording with second Enter, not auto silence.
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f"{MODEL}/tokens.txt",
        encoder=f"{MODEL}/encoder.rknn",
        decoder=f"{MODEL}/decoder.rknn",
        joiner=f"{MODEL}/joiner.rknn",
        provider=os.environ.get("SHERPA_PROVIDER", "rknn"),
        num_threads=int(os.environ.get("SHERPA_NUM_THREADS", "1")),
        sample_rate=SR,
        feature_dim=80,
        enable_endpoint_detection=False,
    )


def _rec_text(rec: sherpa_onnx.OnlineRecognizer, st) -> str:
    r = rec.get_result(st)
    return (r if isinstance(r, str) else getattr(r, "text", str(r))).strip()


def _ptt_min_chars() -> int:
    try:
        return max(1, int(os.environ.get("VOICE_PTT_MIN_CHARS", "2")))
    except ValueError:
        return 2


def _ptt_max_sec() -> float:
    try:
        return float(os.environ.get("VOICE_PTT_MAX_SEC", "60"))
    except ValueError:
        return 60.0


def _mission_kws_debug() -> bool:
    return os.environ.get("VOICE_MISSION_KWS_DEBUG", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _mission_kws_debug_sec() -> float:
    try:
        return float(os.environ.get("VOICE_MISSION_KWS_DEBUG_SEC", "3"))
    except ValueError:
        return 3.0


def _samples_rms(samples: list[float]) -> float:
    if not samples:
        return 0.0
    energy = sum(x * x for x in samples)
    return (energy / len(samples)) ** 0.5


def _read_mic_samples(
    capture: OnDemandAudioCapture,
    timeout_sec: float,
    wake_gate: wake.WakeGate | None = None,
) -> list[float]:
    raw = capture.read_samples(timeout_sec)
    if not raw:
        return raw
    if denoise_enabled():
        out = preprocess_samples(raw, sample_rate=SR)
        if wake_gate is not None:
            wake_gate.note_noise_floor(get_preprocessor(SR).noise_floor)
        return out
    if wake_gate is not None:
        wake_gate.note_noise_floor(_samples_rms(raw))
    return raw


class _MissionKwsWorkerState:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.wake_hit = threading.Event()
        self.lock = threading.Lock()
        self.last_rms = 0.0
        self.read_count = 0
        self.empty_reads = 0


def _mission_kws_worker(
    wake_gate: wake.WakeGate,
    capture: OnDemandAudioCapture,
    state: _MissionKwsWorkerState,
) -> None:
    """Dedicated thread: read mic + feed KWS so MQTT/main loop cannot starve capture."""
    debug = _mission_kws_debug()
    debug_interval = _mission_kws_debug_sec()
    last_debug = time.monotonic()
    mic_on = False
    try:
        while not state.stop.is_set():
            if not wake_gate.listening:
                if mic_on:
                    capture.stop()
                    mic_on = False
                time.sleep(0.05)
                continue
            if tts.is_busy():
                if mic_on:
                    capture.stop()
                    mic_on = False
                time.sleep(0.05)
                continue
            if not mic_on:
                capture.start()
                mic_on = True
            samples = _read_mic_samples(capture, 0.3, wake_gate)
            with state.lock:
                state.read_count += 1
                if samples:
                    state.last_rms = _samples_rms(samples)
                else:
                    state.empty_reads += 1
            if debug and time.monotonic() - last_debug >= debug_interval:
                with state.lock:
                    rms = state.last_rms
                    reads = state.read_count
                    empty = state.empty_reads
                extra = ""
                if denoise_enabled():
                    pp = get_preprocessor(SR)
                    extra = (
                        f" nf={pp.noise_floor:.4f} gain={pp.last_gain:.2f}"
                        f" raw={pp.last_raw_rms:.4f}"
                    )
                print(
                    f"[mission-kws] listening=1 rms={rms:.4f} reads={reads} empty={empty}{extra}",
                    flush=True,
                )
                last_debug = time.monotonic()
            if samples and wake_gate.feed(SR, samples):
                state.wake_hit.set()
                return
    finally:
        if mic_on:
            capture.stop()


def _wait_kws_wake(wake_gate: wake.WakeGate, capture: OnDemandAudioCapture) -> bool:
    if not wake_gate.enabled:
        print("[wake] disabled; entering dialog mode", flush=True)
        return True
    if not wake_gate.kws_available:
        print("[error] KWS unavailable; check VOICE_WAKE_MODEL_DIR", flush=True)
        return False
    wake_gate.resume_boot_listen()
    print("[session] 待机，请说唤醒词（如：你好小诺）", flush=True)
    capture.start()
    try:
        while True:
            if tts.is_busy():
                drain_capture(capture)
                time.sleep(0.05)
                continue
            samples = _read_mic_samples(capture, 0.3, wake_gate)
            if not samples:
                continue
            if wake_gate.feed(SR, samples):
                return True
    finally:
        capture.stop()


def _wait_ui_or_kws_wake(
    wake_gate: wake.WakeGate,
    capture: OnDemandAudioCapture,
    trigger: UiPushToTalkTrigger,
) -> bool:
    trigger._sync_seq()
    wake_seq = trigger._wake_seq
    poll = float(os.environ.get("VOICE_UI_PTT_POLL_SEC", "0.12"))

    if wake_gate.kws_available:
        wake_gate.resume_boot_listen()
        print("[session] 待机：主屏点「语音输入」或说唤醒词", flush=True)
    else:
        print("[session] 待机：主屏点「语音输入」唤醒", flush=True)

    capture.start()
    try:
        while True:
            if tts.is_busy():
                drain_capture(capture)
                time.sleep(0.05)
                continue
            try:
                st = trigger._status()
                w = int(st.get("wake_seq", 0) or 0)
                if w > wake_seq:
                    print("[UI-PTT] UI wake", flush=True)
                    return True
            except Exception as exc:
                print(f"[UI-PTT] poll error: {exc}", flush=True)

            if wake_gate.kws_available:
                samples = _read_mic_samples(capture, 0.15, wake_gate)
                if samples and wake_gate.feed(SR, samples):
                    print("[wake] KWS detected", flush=True)
                    trigger.awake_sync()
                    return True
            else:
                time.sleep(poll)
    finally:
        capture.stop()


def _wait_wake(
    wake_gate: wake.WakeGate,
    capture: OnDemandAudioCapture,
    trigger,
) -> bool:
    if not wake_gate.enabled:
        if ui_input_mode() and isinstance(trigger, UiPushToTalkTrigger):
            return trigger.wait_wake(hint="[输入] 主屏点「语音输入」唤醒…")
        print("[wake] disabled; entering dialog mode", flush=True)
        return True
    if ui_input_mode() and isinstance(trigger, UiPushToTalkTrigger):
        return _wait_ui_or_kws_wake(wake_gate, capture, trigger)
    return _wait_kws_wake(wake_gate, capture)


def _mission_kws_loop(
    wake_gate: wake.WakeGate,
    capture: OnDemandAudioCapture,
    agent: VoiceNavAgent,
) -> str:
    """Returns: 'stop' if wake stop, 'idle' if mission ended naturally."""
    wake_gate.resume_mission_listen()
    print("[导览] 导航中，喊唤醒词可截停", flush=True)
    worker_state = _MissionKwsWorkerState()
    worker = threading.Thread(
        target=_mission_kws_worker,
        args=(wake_gate, capture, worker_state),
        daemon=True,
        name="mission-kws",
    )
    worker.start()
    try:
        while agent.mission_active():
            if agent.nav_session.tick_watchdog(agent.nav.nav_state):
                print("[导览] 看门狗: 导航已结束", flush=True)
                return "idle"
            if worker_state.wake_hit.is_set():
                agent.handle_wake_stop_mission()
                wake_gate.pause_after_wake()
                return "stop"
            time.sleep(0.05)
    finally:
        worker_state.stop.set()
        worker.join(timeout=2.0)
        capture.stop()
        wake_gate.pause_after_wake()
    return "idle"


def _record_until_enter_stop(
    rec: sherpa_onnx.OnlineRecognizer,
    capture: OnDemandAudioCapture,
    trigger,
) -> str:
    """
    First Enter already pressed. Collect all speech until second Enter.
    (UI: same as hold-to-talk release.)
    """
    max_sec = _ptt_max_sec()
    st = rec.create_stream()
    stop = threading.Event()
    last_partial = [""]

    def pump_audio() -> None:
        deadline = time.monotonic() + max_sec
        while not stop.is_set() and time.monotonic() < deadline:
            samples = _read_mic_samples(capture, 0.15)
            if not samples:
                continue
            st.accept_waveform(SR, samples)
            while rec.is_ready(st):
                rec.decode_stream(st)
            partial = _rec_text(rec, st)
            if partial and partial != last_partial[0]:
                last_partial[0] = partial
                print(f"\r... {partial}", end="", flush=True)
                publish_ptt_partial(partial)

    print("[录音] 请说话，说完后按回车上传分析…", flush=True)
    try:
        capture.start()
    except RuntimeError as exc:
        print(f"[error] mic start failed: {exc}", flush=True)
        return ""

    pump = threading.Thread(target=pump_audio, daemon=True)
    pump.start()

    ok = True
    try:
        ok = trigger.wait_finish(hint="[输入] 说完后按回车上传分析…")
    except KeyboardInterrupt:
        ok = False
    finally:
        stop.set()
        capture.stop()
        pump.join(timeout=2.0)

    print(flush=True)
    if not ok:
        rec.reset(st)
        return ""

    tail = int(SR * 0.8)
    st.accept_waveform(SR, [0.0] * tail)
    while rec.is_ready(st):
        rec.decode_stream(st)

    final = _rec_text(rec, st).strip() or last_partial[0].strip()
    rec.reset(st)
    if final:
        publish_ptt_final(final)
    return final


def main() -> None:
    bootstrap_voice_nav_env()
    normalize_voice_nav_env()
    input_mode = os.environ.get("VOICE_INPUT_MODE", "ui").strip().lower()

    host = norm_env(os.environ.get("AI_CAR_LLM_HOST", "http://127.0.0.1:8001"), default="http://127.0.0.1:8001")
    path = norm_env(os.environ.get("AI_CAR_LLM_PATH", "/rkllm_chat"), default="/rkllm_chat")

    if input_mode in ("ui", "screen", "onboard"):
        print("语音导览 — UI 按键模式（主屏「语音输入」开始/结束）", flush=True)
        print(f"  流程: 唤醒 -> UI点语音 -> 说话 -> 再点结束 -> 大模型", flush=True)
    else:
        print("语音导览 — 双回车模式（回车开始录 / 回车结束上传）", flush=True)
        print(f"  流程: 唤醒 -> 回车(开始) -> 说话 -> 回车(上传) -> 大模型", flush=True)
    print(f"  wake: VOICE_WAKE_ENABLED={wake.wake_enabled()}", flush=True)
    if wake.wake_enabled():
        print(f"  wake words: {','.join(wake.wake_words())}", flush=True)
    print(f"  denoise:  VOICE_NAV_DENOISE={denoise_enabled()}", flush=True)
    print("  nav MQTT: tour_nav (API优先) / robot/nav_room (本地回退)", flush=True)
    if ui_input_mode():
        from voice_nav.tour_api_client import TourApiClient

        ptt_api = TourApiClient.from_env()
        print(f"  [UI-PTT] backend={ptt_api.base}", flush=True)
        if ptt_api.reachable(force=True):
            print("  [UI-PTT] backend reachable", flush=True)
        else:
            print(
                f"  [UI-PTT] ERROR: cannot reach {ptt_api.base}",
                flush=True,
            )
            print(
                "  [UI-PTT] fix: export COURIER_API_BASE=http://<PC_IP>:8000",
                flush=True,
            )

    if os.environ.get("VOICE_NAV_STARTUP_DONE", "").strip().lower() in ("1", "true", "yes"):
        startup = check_startup(host=host, path=path, force=True)
        apply_startup(startup)
        print(f"[startup] backend={startup.backend} reason={startup.reason}", flush=True)
    else:
        startup = check_startup(host=host, path=path, force=True)
        apply_startup(startup)
        speak_startup = os.environ.get("VOICE_NAV_TTS_STARTUP", "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        announce_startup(
            startup,
            speak=speak_startup,
            print_json=os.environ.get("VOICE_NAV_STARTUP_JSON", "0").strip().lower() in ("1", "true", "yes"),
        )
    if not startup.ok:
        sys.exit(1)

    print("[STT] loading recognizer...", flush=True)
    rec = _make_recognizer()
    print("[STT] ready", flush=True)

    capture = OnDemandAudioCapture.from_env()
    trigger = make_input_trigger()
    agent = VoiceNavAgent()
    agent.nav.ensure_monitoring()
    wake_gate = wake.WakeGate()
    warmed = False
    min_chars = _ptt_min_chars()

    voice_state = STATE_KWS_BOOT if wake_gate.enabled else STATE_IDLE

    try:
        while True:
            while agent.mission_active():
                reason = _mission_kws_loop(wake_gate, capture, agent)
                voice_state = STATE_IDLE
                if reason == "stop":
                    print("[导览] 已截停，按回车开始录音", flush=True)
                else:
                    print("[导览] 已到达目的地，按回车继续对话", flush=True)

            if voice_state == STATE_KWS_BOOT:
                if not _wait_wake(wake_gate, capture, trigger):
                    break
                wake_gate.pause_after_wake()
                tts.speak_key("fixed:wake_reply", fallback_text=wake_reply(), tier="status")
                if agent.tour_coord:
                    ok, msg = agent.tour_coord.on_kws_wake()
                    print(f"[tour-api] wake: {ok} {msg}", flush=True)
                if ui_input_mode():
                    publish_ptt_awake_sync()
                voice_state = STATE_IDLE
                if ui_input_mode():
                    print("[会话] 已唤醒，请再次点击「语音输入」开始说话", flush=True)
                else:
                    print("[会话] 已唤醒，按回车开始录音", flush=True)

            hint_begin = (
                "[输入] 主屏点「语音输入」开始说话…"
                if ui_input_mode()
                else "[输入] 按回车开始录音（q 退出）…"
            )
            if not trigger.wait_trigger(hint=hint_begin):
                break

            if agent.tour_coord:
                agent.tour_coord.on_ptt_begin()

            while agent.is_busy() or tts.is_busy():
                time.sleep(0.1)

            utterance = _record_until_enter_stop(rec, capture, trigger)
            if not utterance:
                print("[跳过] 未识别到语音", flush=True)
                continue

            norm = normalize_spoken_text(utterance)
            if norm != utterance:
                print(f"[STT] {utterance}", flush=True)
                print(f"  [norm] {norm}", flush=True)
                utterance = norm
            else:
                print(f"[STT] {utterance}", flush=True)

            if len(utterance.strip()) < min_chars:
                print(f"  [skip] too short (min {min_chars} chars)", flush=True)
                continue

            try:
                warmed, session_event = agent.handle_text(
                    utterance,
                    host=host,
                    path=path,
                    warmed=warmed,
                )
                if session_event == "end_session":
                    voice_state = STATE_KWS_BOOT
                    publish_ptt_sleep()
                    if ui_input_mode():
                        print("[会话] 已结束，请点「语音输入」唤醒…", flush=True)
                    else:
                        print("[会话] 已结束，等待唤醒词…", flush=True)
            except Exception as exc:
                print(f"  [error] {exc}", flush=True)
    finally:
        capture.close()
        agent.close()


if __name__ == "__main__":
    main()
