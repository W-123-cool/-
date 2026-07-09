# -*- coding: utf-8 -*-
"""Normalize env vars and optionally load voice_nav_env.sh."""
from __future__ import annotations

import os
import subprocess
from typing import Iterable

_VOICE_NAV_PREFIXES = (
    "DASHSCOPE_",
    "VOICE_NAV_",
    "VOICE_WAKE_",
    "VOICE_SESSION_",
    "AI_CAR_LLM_",
)


def norm_env(value: str | None, *, default: str = "") -> str:
    if value is None:
        return default
    return value.replace("\r", "").replace("\n", "").strip() or default


def _should_bootstrap_key(key: str) -> bool:
    return key.startswith(_VOICE_NAV_PREFIXES)


def bootstrap_voice_nav_env() -> None:
    """Load scripts/voice_nav_env.sh when cloud/local LLM vars are missing."""
    if norm_env(os.environ.get("DASHSCOPE_API_KEY")) and norm_env(
        os.environ.get("DASHSCOPE_BASE_URL")
    ):
        return
    ros_ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.environ.get("VOICE_NAV_ENV_FILE", "").strip(),
        os.path.join(ros_ws, "scripts", "voice_nav_env.sh"),
    ]
    env_sh = next((p for p in candidates if p and os.path.isfile(p)), "")
    if not env_sh:
        return
    try:
        proc = subprocess.run(
            ["bash", "-c", f'set -a; source "{env_sh}"; env -0'],
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if proc.returncode != 0:
        return
    raw = proc.stdout
    i = 0
    n = len(raw)
    while i < n:
        j = raw.find(b"\0", i)
        if j < 0:
            break
        chunk = raw[i:j].decode("utf-8", errors="replace")
        i = j + 1
        if "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        if not _should_bootstrap_key(key):
            continue
        if not norm_env(os.environ.get(key)):
            os.environ[key] = norm_env(val)


def normalize_voice_nav_env(keys: Iterable[str] | None = None) -> None:
    default_keys = (
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "VOICE_NAV_BACKEND",
        "VOICE_NAV_USE_LLM",
        "AI_CAR_LLM_HOST",
        "AI_CAR_LLM_PATH",
    )
    for key in keys or default_keys:
        if key in os.environ:
            os.environ[key] = norm_env(os.environ.get(key))
