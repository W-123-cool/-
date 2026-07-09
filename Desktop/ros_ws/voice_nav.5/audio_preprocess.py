# -*- coding: utf-8 -*-
"""Lightweight mic preprocessing: high-pass, noise gate, AGC for moving robot."""
from __future__ import annotations

import math
import os
import time
from typing import List, Optional


def _env_on(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def denoise_enabled() -> bool:
    return _env_on("VOICE_NAV_DENOISE", "1")


def denoise_debug() -> bool:
    return _env_on("VOICE_NAV_DENOISE_DEBUG", "0")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _rms(samples: List[float]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(x * x for x in samples) / len(samples))


class _OnePoleHighPass:
    """Remove low-frequency motor / wheel rumble."""

    def __init__(self, sample_rate: int, cutoff_hz: float) -> None:
        rc = 1.0 / (2.0 * math.pi * max(50.0, cutoff_hz))
        dt = 1.0 / max(8000, sample_rate)
        self._alpha = rc / (rc + dt)
        self._prev_x = 0.0
        self._prev_y = 0.0

    def process(self, samples: List[float]) -> List[float]:
        if not samples:
            return samples
        out: List[float] = []
        alpha = self._alpha
        prev_x = self._prev_x
        prev_y = self._prev_y
        for x in samples:
            y = alpha * (prev_y + x - prev_x)
            out.append(y)
            prev_x = x
            prev_y = y
        self._prev_x = prev_x
        self._prev_y = prev_y
        return out


class AudioPreprocessor:
    """
    Streaming denoise for 16 kHz mono float samples.

    Pipeline: high-pass -> noise-floor tracking -> soft gate -> adaptive AGC.
    """

    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self._highpass_hz = _float_env("VOICE_NAV_DENOISE_HIGHPASS_HZ", 300.0)
        self._gate_enabled = _env_on("VOICE_NAV_DENOISE_GATE", "1")
        self._agc_enabled = _env_on("VOICE_NAV_DENOISE_AGC", "1")
        self._target_rms = _float_env("VOICE_NAV_DENOISE_TARGET_RMS", 0.06)
        self._max_gain = _float_env("VOICE_NAV_DENOISE_MAX_GAIN", 4.0)
        self._noise_alpha = _float_env("VOICE_NAV_DENOISE_NOISE_ALPHA", 0.92)
        self._speech_ratio = _float_env("VOICE_NAV_DENOISE_SPEECH_RATIO", 2.8)
        self._quiet_ref = _float_env("VOICE_NAV_DENOISE_QUIET_RMS", 0.012)
        self._hp = _OnePoleHighPass(sample_rate, self._highpass_hz)
        self._noise_floor = self._quiet_ref
        self._last_raw_rms = 0.0
        self._last_out_rms = 0.0
        self._last_gain = 1.0
        self._last_log = 0.0

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    @property
    def last_gain(self) -> float:
        return self._last_gain

    @property
    def last_raw_rms(self) -> float:
        return self._last_raw_rms

    @property
    def last_out_rms(self) -> float:
        return self._last_out_rms

    def reset(self) -> None:
        self._hp = _OnePoleHighPass(self.sample_rate, self._highpass_hz)
        self._noise_floor = self._quiet_ref
        self._last_gain = 1.0

    def _update_noise_floor(self, rms: float) -> None:
        if rms <= 0.0:
            return
        if rms < self._noise_floor * self._speech_ratio:
            alpha = self._noise_alpha
            self._noise_floor = alpha * self._noise_floor + (1.0 - alpha) * rms
        floor_min = self._quiet_ref * 0.25
        floor_max = _float_env("VOICE_NAV_DENOISE_NOISE_MAX", 0.25)
        self._noise_floor = max(floor_min, min(floor_max, self._noise_floor))

    def _soft_gate(self, samples: List[float], rms: float) -> List[float]:
        if not self._gate_enabled or rms <= 0.0:
            return samples
        floor = max(self._noise_floor, 1e-6)
        snr = rms / floor
        if snr < 1.2:
            atten = 0.12
        elif snr < self._speech_ratio:
            atten = 0.12 + 0.88 * (snr - 1.2) / max(0.1, self._speech_ratio - 1.2)
        else:
            atten = 1.0
        return [s * atten for s in samples]

    def _apply_agc(self, samples: List[float], rms: float) -> List[float]:
        if not self._agc_enabled or rms <= 1e-6:
            return samples
        floor = max(self._noise_floor, self._quiet_ref * 0.5)
        noise_boost = min(self._max_gain, math.sqrt(max(floor, 1e-6) / self._quiet_ref))
        level_gain = min(self._max_gain, self._target_rms / rms)
        gain = min(self._max_gain, max(1.0, level_gain * math.sqrt(noise_boost)))
        self._last_gain = gain
        out: List[float] = []
        for s in samples:
            v = s * gain
            if v > 1.0:
                v = 1.0
            elif v < -1.0:
                v = -1.0
            out.append(v)
        return out

    def process(self, samples: List[float]) -> List[float]:
        if not samples:
            return samples
        raw_rms = _rms(samples)
        self._last_raw_rms = raw_rms
        self._update_noise_floor(raw_rms)

        x = self._hp.process(samples)
        hp_rms = _rms(x)
        x = self._soft_gate(x, hp_rms)
        gate_rms = _rms(x)
        x = self._apply_agc(x, gate_rms)
        self._last_out_rms = _rms(x)

        if denoise_debug() and time.monotonic() - self._last_log >= 3.0:
            print(
                f"[denoise] raw_rms={raw_rms:.4f} out_rms={self._last_out_rms:.4f} "
                f"nf={self._noise_floor:.4f} gain={self._last_gain:.2f}",
                flush=True,
            )
            self._last_log = time.monotonic()
        return x


_preprocessor: Optional[AudioPreprocessor] = None


def get_preprocessor(sample_rate: int = 16000) -> AudioPreprocessor:
    global _preprocessor
    if _preprocessor is None:
        _preprocessor = AudioPreprocessor(sample_rate=sample_rate)
    return _preprocessor


def preprocess_samples(samples: List[float], *, sample_rate: int = 16000) -> List[float]:
    if not denoise_enabled() or not samples:
        return samples
    return get_preprocessor(sample_rate).process(samples)


def reset_preprocessor() -> None:
    global _preprocessor
    if _preprocessor is not None:
        _preprocessor.reset()
