"""TTS: Sherpa-onnx Matcha (natural) with espeak-ng fallback."""
from __future__ import annotations

import os
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from typing import Any, Callable, Optional, Union

_tts_lock = threading.Lock()
_tts_queue: queue.Queue[Optional[Union[str, Callable[[], None]]]] = queue.Queue()
_tts_worker: Optional[threading.Thread] = None
_speaking = threading.Event()
_cancel_requested = False
_play_proc: Optional[subprocess.Popen] = None
_sherpa_tts: Any = None
_sherpa_init_failed = False


def _expand(path: str) -> str:
    return os.path.expanduser(path.strip())


def _default_model_dir() -> str:
    return _expand(
        os.environ.get(
            "SHERPA_TTS_MODEL",
            "~/Desktop/rk3588-offline-bundle/model/matcha-icefall-zh-baker",
        )
    )


def _default_vocoder() -> str:
    return _expand(
        os.environ.get(
            "SHERPA_TTS_VOCODER",
            "~/Desktop/rk3588-offline-bundle/model/vocos-22khz-univ.onnx",
        )
    )


def _tts_backend() -> str:
    return os.environ.get("VOICE_NAV_TTS_BACKEND", "auto").strip().lower()


def _use_sherpa() -> bool:
    backend = _tts_backend()
    if backend in ("0", "false", "no", "espeak", "off"):
        return False
    if backend in ("1", "true", "yes", "sherpa", "matcha", "on"):
        return True
    model_dir = _default_model_dir()
    vocoder = _default_vocoder()
    acoustic = os.path.join(model_dir, "model-steps-3.onnx")
    return (
        os.path.isfile(acoustic)
        and os.path.isfile(vocoder)
        and os.path.isfile(os.path.join(model_dir, "lexicon.txt"))
        and os.path.isfile(os.path.join(model_dir, "tokens.txt"))
    )


def _tts_env() -> dict[str, str]:
    env = os.environ.copy()
    sink = os.environ.get("VOICE_NAV_PULSE_SINK", "").strip()
    if sink:
        env["PULSE_SINK"] = sink
    return env


def _espeak_rate() -> str:
    return os.environ.get("VOICE_NAV_TTS_RATE", "150").strip() or "150"


def _rule_fsts(model_dir: str) -> str:
    custom = os.environ.get("SHERPA_TTS_RULE_FSTS", "").strip()
    if custom:
        return custom
    names = ("phone.fst", "date.fst", "number.fst")
    parts = [os.path.join(model_dir, n) for n in names if os.path.isfile(os.path.join(model_dir, n))]
    return ",".join(parts)


def _get_sherpa_tts() -> Any:
    global _sherpa_tts, _sherpa_init_failed
    if _sherpa_tts is not None:
        return _sherpa_tts
    if _sherpa_init_failed:
        raise RuntimeError("Sherpa TTS init previously failed")

    import sherpa_onnx

    model_dir = _default_model_dir()
    vocoder = _default_vocoder()
    num_threads = int(os.environ.get("SHERPA_TTS_NUM_THREADS", "2"))
    provider = os.environ.get("SHERPA_TTS_PROVIDER", "cpu").strip() or "cpu"

    tts_config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(
                acoustic_model=os.path.join(model_dir, "model-steps-3.onnx"),
                vocoder=vocoder,
                lexicon=os.path.join(model_dir, "lexicon.txt"),
                tokens=os.path.join(model_dir, "tokens.txt"),
            ),
            num_threads=num_threads,
            provider=provider,
            debug=os.environ.get("SHERPA_TTS_DEBUG", "0").strip() in ("1", "true", "yes"),
        ),
        rule_fsts=_rule_fsts(model_dir),
        max_num_sentences=int(os.environ.get("SHERPA_TTS_MAX_SENTENCES", "1")),
    )
    if not tts_config.validate():
        _sherpa_init_failed = True
        raise RuntimeError("Sherpa TTS config validate() failed; check model paths")

    _sherpa_tts = sherpa_onnx.OfflineTts(tts_config)
    print(f"[TTS] Sherpa Matcha ready: {model_dir}", flush=True)
    return _sherpa_tts


def _write_wav(path: str, samples: list[float], sample_rate: int) -> None:
    if hasattr(samples, "tolist"):
        samples = samples.tolist()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for s in samples:
            val = int(max(-1.0, min(1.0, float(s))) * 32767)
            frames.extend(struct.pack("<h", val))
        wf.writeframes(frames)


def _play_wav(path: str) -> None:
    global _play_proc, _cancel_requested
    if _cancel_requested:
        return
    env = _tts_env()
    pulse_sink = os.environ.get("VOICE_NAV_PULSE_SINK", "").strip()
    alsa_dev = os.environ.get("VOICE_NAV_ALSA_DEVICE", "").strip()
    force_alsa = os.environ.get("VOICE_NAV_FORCE_ALSA", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    if alsa_dev and shutil.which("aplay") and (force_alsa or not pulse_sink):
        _play_proc = subprocess.Popen(
            ["aplay", "-q", "-D", alsa_dev, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _play_proc.wait()
        _play_proc = None
        return
    if pulse_sink and shutil.which("paplay"):
        _play_proc = subprocess.Popen(
            ["paplay", "--device=" + pulse_sink, path],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _play_proc.wait()
        _play_proc = None
        return
    if shutil.which("paplay"):
        _play_proc = subprocess.Popen(
            ["paplay", path],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _play_proc.wait()
        _play_proc = None
        return
    if alsa_dev and shutil.which("aplay"):
        _play_proc = subprocess.Popen(
            ["aplay", "-q", "-D", alsa_dev, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _play_proc.wait()
        _play_proc = None


def _sherpa_speed() -> float:
    try:
        return float(os.environ.get("VOICE_NAV_TTS_SPEED", "1.0"))
    except ValueError:
        return 1.0


def _sherpa_sid() -> int:
    try:
        return int(os.environ.get("VOICE_NAV_TTS_SPEAKER", "0"))
    except ValueError:
        return 0


def _speak_sherpa_sync(text: str) -> bool:
    try:
        tts = _get_sherpa_tts()
    except Exception as exc:
        print(f"[TTS] Sherpa init failed: {exc}", flush=True)
        return False

    try:
        import sherpa_onnx

        gen = sherpa_onnx.GenerationConfig()
        gen.sid = _sherpa_sid()
        gen.speed = _sherpa_speed()
        audio = tts.generate(text, gen)
    except TypeError:
        audio = tts.generate(text, sid=_sherpa_sid(), speed=_sherpa_speed())

    if not audio or len(audio.samples) == 0:
        print("[TTS] Sherpa returned empty audio", flush=True)
        return False

    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="voice_nav_tts_")
        os.close(fd)
        _write_wav(tmp_path, audio.samples, audio.sample_rate)
        _play_wav(tmp_path)
        return True
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _speak_espeak_sync(text: str) -> None:
    rate = _espeak_rate()
    env = _tts_env()
    alsa_dev = os.environ.get("VOICE_NAV_ALSA_DEVICE", "").strip()
    pulse_sink = os.environ.get("VOICE_NAV_PULSE_SINK", "").strip()

    if pulse_sink and shutil.which("paplay"):
        proc = subprocess.run(
            ["espeak-ng", "-v", "zh", "-s", rate, text, "--stdout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            subprocess.run(
                ["paplay"],
                input=proc.stdout,
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

    if alsa_dev and shutil.which("aplay"):
        espeak = subprocess.Popen(
            ["espeak-ng", "-v", "zh", "-s", rate, text, "--stdout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["aplay", "-q", "-D", alsa_dev],
            stdin=espeak.stdout,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        espeak.wait()
        return

    subprocess.run(
        ["espeak-ng", "-v", "zh", "-s", rate, text],
        env=env,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_tts(text: str) -> None:
    global _cancel_requested
    if _cancel_requested:
        return
    with _tts_lock:
        if _use_sherpa() and _speak_sherpa_sync(text):
            return
        if shutil.which("espeak-ng"):
            _speak_espeak_sync(text)
            return
        if shutil.which("spd-say"):
            subprocess.run(
                ["spd-say", "-l", "zh-CN", text],
                env=_tts_env(),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _tts_worker_loop() -> None:
    while True:
        job = _tts_queue.get()
        try:
            if job is None:
                return
            _speaking.set()
            if callable(job):
                job()
            else:
                _run_tts(str(job))
        finally:
            _speaking.clear()
            _tts_queue.task_done()


def _ensure_tts_worker() -> None:
    global _tts_worker
    if _tts_worker is not None and _tts_worker.is_alive():
        return
    _tts_worker = threading.Thread(target=_tts_worker_loop, name="voice_nav_tts", daemon=True)
    _tts_worker.start()


def is_busy() -> bool:
    return _speaking.is_set() or not _tts_queue.empty()


def stop() -> None:
    """Interrupt playback and clear pending TTS."""
    global _cancel_requested, _play_proc
    _cancel_requested = True
    while True:
        try:
            _tts_queue.get_nowait()
            _tts_queue.task_done()
        except queue.Empty:
            break
    proc = _play_proc
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _play_proc = None
    _speaking.clear()
    _cancel_requested = False


def wait_until_idle(timeout: float | None = None) -> bool:
    if timeout is None:
        timeout = float(os.environ.get("VOICE_NAV_TTS_WAIT_SEC", "120"))
    deadline = time.monotonic() + timeout if timeout > 0 else None
    while is_busy():
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _sanitize_tts_text(text: str) -> str:
    for ch in ("\u300c", "\u300d", "\u201c", "\u201d", "\u2018", "\u2019"):
        text = text.replace(ch, "")
    return text.strip()


def _status_backend() -> str:
    return os.environ.get("VOICE_NAV_TTS_STATUS_BACKEND", "cache").strip().lower()


def _dialog_backend() -> str:
    return os.environ.get("VOICE_NAV_TTS_DIALOG_BACKEND", "matcha").strip().lower()


def _enqueue_or_sync(fn) -> None:
    if os.environ.get("VOICE_NAV_TTS_SYNC", "1").strip().lower() in ("1", "true", "yes"):
        _speaking.set()
        try:
            fn()
        finally:
            _speaking.clear()
        return
    _ensure_tts_worker()
    _tts_queue.put(fn)


def _play_cached_wav_sync(path: str) -> None:
    global _cancel_requested
    if _cancel_requested:
        return
    _play_wav(path)


def _speak_light_sync(text: str) -> None:
    if shutil.which("espeak-ng"):
        _speak_espeak_sync(text)
        return
    _run_tts(text)


def _speak_dialog_sync(text: str) -> None:
    backend = _dialog_backend()
    if backend in ("espeak", "light"):
        _speak_light_sync(text)
        return
    if backend in ("0", "false", "off"):
        return
    _run_tts(text)


def speak_cached_file(path: str, *, label: str = "") -> None:
    if os.environ.get("VOICE_NAV_TTS", "1").strip().lower() in ("0", "false", "no"):
        return
    tag = label or path
    print(f"[TTS/cache] {tag}", flush=True)

    def _run() -> None:
        _play_cached_wav_sync(path)

    _enqueue_or_sync(_run)


def speak_key(cache_key: str, *, fallback_text: str | None = None, tier: str = "status") -> bool:
    """Play pre-generated wav. Returns True if cache hit (or fallback spoken)."""
    from . import audio_cache

    if os.environ.get("VOICE_NAV_TTS", "1").strip().lower() in ("0", "false", "no"):
        return False
    if tier == "status" and os.environ.get("VOICE_NAV_TTS_STATUS", "1").strip().lower() in (
        "0",
        "false",
    ):
        return False

    path = audio_cache.resolve_path(cache_key)
    text = fallback_text or audio_cache.entry_text(cache_key) or ""
    if path:
        speak_cached_file(path, label=cache_key)
        return True

    if not text:
        print(f"[TTS/cache] miss (no fallback): {cache_key}", flush=True)
        return False

    print(f"[TTS/cache] miss -> {tier}: {cache_key}", flush=True)

    def _run() -> None:
        if tier == "status" and _status_backend() in ("cache", "espeak", "light"):
            _speak_light_sync(text)
        elif tier == "dialog":
            _speak_dialog_sync(text)
        else:
            _run_tts(text)

    _enqueue_or_sync(_run)
    return False


def speak_routed(
    *,
    cache_key: str | None = None,
    text: str | None = None,
    source: str = "llm",
    tier: str | None = None,
) -> None:
    """Unified speak entry: cache_key first, then text by tier."""
    if cache_key:
        fb = (text or "").strip() or None
        if speak_key(cache_key, fallback_text=fb, tier=tier or ("status" if source == "system" else "dialog")):
            return
    body = _sanitize_tts_text((text or "").strip())
    if not body:
        return
    resolved_tier = tier or ("status" if source in ("system", "rule") else "dialog")

    def _run() -> None:
        if resolved_tier == "status" and _status_backend() in ("cache", "espeak", "light"):
            _speak_light_sync(body)
        elif resolved_tier == "dialog":
            _speak_dialog_sync(body)
        else:
            _run_tts(body)

    print(f"[TTS] {body}", flush=True)
    if os.environ.get("VOICE_NAV_TTS", "1").strip().lower() in ("0", "false", "no"):
        return
    _enqueue_or_sync(_run)


def speak(text: str) -> None:
    speak_routed(text=text, source="llm", tier="dialog")
