# -*- coding: utf-8 -*-
"""On-demand microphone capture (no always-on arecord pipe)."""
from __future__ import annotations

import os
import re
import select
import struct
import subprocess
import time
from typing import List, Optional
def _mic_substr_pattern() -> str:
    return os.environ.get(
        "VOICE_NAV_MIC_SUBSTR",
        r"AB13X|Generic.*USB.*Audio|AB13X_USB_Audio",
    )


def _match_pulse_source(pattern: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        rx = re.compile(pattern, re.I)
        for line in (out.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[1]
            if "monitor" in name.lower():
                continue
            if rx.search(name):
                return f"pulse:{name}"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def detect_mic_device() -> Optional[str]:
    """Auto-detect USB mic (AB13X) or Astra/Orbbec (same logic as ai_car_detect_astra_device_or_die)."""
    found = _match_pulse_source(_mic_substr_pattern())
    if found:
        return found

    try:
        out = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        text = out.stdout or ""
        for line in text.splitlines():
            if re.search(r"ab13x|generic.*usb.*audio|astra|orbbec", line, re.I):
                m = re.match(r"card\s+(\d+):", line.strip(), re.I)
                if m:
                    return f"alsa:plughw:{m.group(1)},0"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    return _match_pulse_source(r"orbbec|astra")


def resolve_mic_device() -> str:
    dev = os.environ.get("AI_CAR_AUDIO_DEV", "").strip()
    if dev:
        return dev
    found = detect_mic_device()
    if found:
        os.environ["AI_CAR_AUDIO_DEV"] = found
        print(f"[mic] auto-detected: {found}", flush=True)
        return found
    raise RuntimeError(
        "AI_CAR_AUDIO_DEV is not set and no USB/Astra mic found.\n"
        "  pactl list sources short   # PipeWire/Pulse source name\n"
        "  export AI_CAR_AUDIO_DEV=pulse:alsa_input.usb-Generic_AB13X_USB_Audio_....mono-fallback\n"
        "  or: export AI_CAR_AUDIO_DEV=alsa:plughw:4,0   # arecord -l"
    )


class OnDemandAudioCapture:
    """Start/stop arecord or parecord only while KWS or PTT recording is active."""

    def __init__(self, device: str, *, sample_rate: int = 16000, chunk_bytes: int = 3200) -> None:
        self.device = (device or "").strip()
        self.sample_rate = sample_rate
        self.chunk_bytes = chunk_bytes
        self._proc: Optional[subprocess.Popen] = None

    @classmethod
    def from_env(cls) -> "OnDemandAudioCapture":
        dev = resolve_mic_device()
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
