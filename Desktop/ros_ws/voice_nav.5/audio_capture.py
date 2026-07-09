# -*- coding: utf-8 -*-
"""On-demand microphone capture (no always-on arecord pipe)."""
from __future__ import annotations

import os
import select
import struct
import subprocess
import time
from typing import List, Optional


class OnDemandAudioCapture:
    """Start/stop arecord or parecord only while KWS or PTT recording is active."""

    def __init__(self, device: str, *, sample_rate: int = 16000, chunk_bytes: int = 3200) -> None:
        self.device = (device or "").strip()
        self.sample_rate = sample_rate
        self.chunk_bytes = chunk_bytes
        self._proc: Optional[subprocess.Popen] = None

    @classmethod
    def from_env(cls) -> "OnDemandAudioCapture":
        dev = os.environ.get("AI_CAR_AUDIO_DEV", "").strip()
        if not dev:
            raise RuntimeError("AI_CAR_AUDIO_DEV is not set (microphone device)")
        chunk = int(os.environ.get("VOICE_AUDIO_CHUNK_BYTES", "3200"))
        return cls(dev, chunk_bytes=chunk)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.running:
            return
        if self.device.startswith("alsa:"):
            cmd = [
                "arecord",
                "-D",
                self.device[len("alsa:") :],
                "-f",
                "S16_LE",
                "-r",
                str(self.sample_rate),
                "-c",
                "1",
                "-t",
                "raw",
            ]
        elif self.device.startswith("pulse:"):
            cmd = [
                "parecord",
                f"--device={self.device[len('pulse:'):]}",
                "--format=s16le",
                f"--rate={self.sample_rate}",
                "--channels=1",
                "--raw",
            ]
        else:
            raise RuntimeError(f"Unknown audio device: {self.device}")
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if self._proc.stdout is None:
            raise RuntimeError("arecord/parecord did not provide stdout")
        time.sleep(0.2)
        if self._proc.poll() is not None:
            err = ""
            if self._proc.stderr:
                err = self._proc.stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"arecord failed ({self.device}): {err or 'exit early'}")

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=1.0)
        finally:
            if self._proc.stdout:
                try:
                    self._proc.stdout.close()
                except OSError:
                    pass
            self._proc = None

    def read_chunk(self, timeout_sec: float = 0.5) -> bytes:
        if not self.running or self._proc is None or self._proc.stdout is None:
            return b""
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout_sec)
        if not ready:
            return b""
        return self._proc.stdout.read(self.chunk_bytes) or b""

    def read_samples(self, timeout_sec: float = 0.5) -> List[float]:
        chunk = self.read_chunk(timeout_sec)
        if not chunk:
            return []
        n = len(chunk) // 2
        if n <= 0:
            return []
        return [s / 32768.0 for s in struct.unpack("<" + "h" * n, chunk[: n * 2])]

    def close(self) -> None:
        self.stop()


def drain_capture(capture: OnDemandAudioCapture, max_sec: float = 0.3) -> None:
    if not capture.running:
        return
    deadline = time.monotonic() + max_sec
    while time.monotonic() < deadline:
        if not capture.read_chunk(0.05):
            break
