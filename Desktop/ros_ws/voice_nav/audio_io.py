# -*- coding: utf-8 -*-
"""Microphone capture: start arecord after STT/KWS models are loaded."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import BinaryIO


def _alsa_device_from_env() -> str:
    dev = os.environ.get("AI_CAR_AUDIO_DEV", "").strip()
    if dev.startswith("alsa:"):
        return dev[5:]
    return dev


class _MicReader:
    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._stdout = proc.stdout

    def read(self, size: int) -> bytes:
        if self._stdout is None:
            return b""
        data = self._stdout.read(size)
        if self._proc.poll() is not None and self._proc.stderr is not None:
            err = self._proc.stderr.read().decode("utf-8", errors="replace").strip()
            if err:
                print(f"[arecord] {err}", flush=True)
        return data


def open_mic_stream(*, sample_rate: int = 16000) -> BinaryIO:
    """
    Return a readable binary stream of S16_LE mono PCM.

    If AI_CAR_AUDIO_DEV is set, spawns arecord after models are loaded.
    Otherwise reads legacy stdin pipe from ``arecord | python``.
    """
    alsa = _alsa_device_from_env()
    if not alsa:
        print(
            "[mic] using stdin pipe (set AI_CAR_AUDIO_DEV to open mic after init)",
            flush=True,
        )
        return sys.stdin.buffer

    buffer_ms = os.environ.get("VOICE_ARECORD_BUFFER_MS", "3000").strip() or "3000"
    cmd = [
        "arecord",
        "-D",
        alsa,
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        "1",
        "-t",
        "raw",
        "--buffer-time",
        buffer_ms,
    ]
    print(f"[mic] recording: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    return _MicReader(proc)  # type: ignore[return-value]
