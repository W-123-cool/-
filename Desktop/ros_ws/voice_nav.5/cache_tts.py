# -*- coding: utf-8 -*-
"""Offline synthesis for voice_nav audio cache (Matcha or espeak-ng)."""
from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path


def cache_tts_backend() -> str:
    raw = os.environ.get("VOICE_NAV_CACHE_TTS_BACKEND", "matcha").strip().lower()
    if raw in ("espeak", "light", "espeak-ng"):
        return "espeak"
    return "matcha"


def _espeak_rate() -> str:
    return os.environ.get("VOICE_NAV_TTS_RATE", "150").strip() or "150"


def synth_espeak_wav(text: str, out_path: Path) -> bool:
    if not shutil.which("espeak-ng"):
        print("[cache] espeak-ng not found", flush=True)
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["espeak-ng", "-v", "zh", "-s", _espeak_rate(), "-w", str(out_path), text],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not out_path.is_file():
        return False
    return _wav_nonempty(out_path)


def synth_matcha_wav(text: str, out_path: Path) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    try:
        from .tts import _get_sherpa_tts, _sherpa_sid, _sherpa_speed, _write_wav

        import sherpa_onnx

        tts = _get_sherpa_tts()
        gen = sherpa_onnx.GenerationConfig()
        gen.sid = _sherpa_sid()
        gen.speed = _sherpa_speed()
        try:
            audio = tts.generate(text, gen)
        except TypeError:
            audio = tts.generate(text, sid=_sherpa_sid(), speed=_sherpa_speed())
        if not audio or len(audio.samples) == 0:
            print(f"[cache] Matcha empty audio: {text!r}", flush=True)
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(str(out_path), audio.samples, audio.sample_rate)
        return _wav_nonempty(out_path)
    except Exception as exc:
        print(f"[cache] Matcha synth failed ({text!r}): {exc}", flush=True)
        return False


def _wav_nonempty(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() > 0
    except wave.Error:
        return False


def synth_cache_wav(text: str, out_path: Path, *, backend: str | None = None) -> bool:
    """Synthesize one cache wav. Falls back espeak if Matcha fails."""
    mode = (backend or cache_tts_backend()).strip().lower()
    if mode == "matcha":
        if synth_matcha_wav(text, out_path):
            return True
        print(f"[cache] Matcha miss -> espeak fallback: {text!r}", flush=True)
    return synth_espeak_wav(text, out_path)
