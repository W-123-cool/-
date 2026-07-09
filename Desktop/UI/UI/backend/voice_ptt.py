# -*- coding: utf-8 -*-
"""UI <-> voice_to_nav_agent bridge: wake / PTT begin-end / partial text."""
from __future__ import annotations

import threading
import time
from typing import Any, Literal

_lock = threading.Lock()
_state: dict[str, Any] = {
    "awake": False,
    "wake_seq": 0,
    "begin_seq": 0,
    "end_seq": 0,
    "recording": False,
    "partial": "",
    "final": "",
    "updated_mono": 0.0,
}

PttAction = Literal["wake", "begin", "end", "noop"]


def _touch() -> None:
    _state["updated_mono"] = time.monotonic()


def ptt_reset() -> None:
    with _lock:
        _state["awake"] = False
        _state["wake_seq"] = 0
        _state["begin_seq"] = 0
        _state["end_seq"] = 0
        _state["recording"] = False
        _state["partial"] = ""
        _state["final"] = ""
        _touch()


def ptt_awake_sync() -> tuple[bool, str]:
    """Voice agent reports wake (KWS); sync UI without bumping wake_seq."""
    with _lock:
        _state["awake"] = True
        _state["recording"] = False
        _state["partial"] = ""
        _state["final"] = ""
        _touch()
    return True, "awake synced"


def ptt_sleep() -> tuple[bool, str]:
    """Session ended; UI voice button returns to idle."""
    with _lock:
        _state["awake"] = False
        _state["recording"] = False
        _state["partial"] = ""
        _state["final"] = ""
        _touch()
    return True, "session sleep"


def ptt_wake() -> tuple[bool, str]:
    with _lock:
        _state["awake"] = True
        _state["wake_seq"] = int(_state["wake_seq"]) + 1
        _state["recording"] = False
        _state["partial"] = ""
        _state["final"] = ""
        _touch()
        seq = int(_state["wake_seq"])
    return True, f"UI wake (seq={seq})"


def ptt_begin() -> tuple[bool, str]:
    with _lock:
        if not _state["awake"]:
            return False, "tap voice button to wake first"
        _state["begin_seq"] = int(_state["begin_seq"]) + 1
        _state["recording"] = True
        _state["partial"] = ""
        _state["final"] = ""
        _touch()
        seq = int(_state["begin_seq"])
    return True, f"PTT begin (seq={seq})"


def ptt_end(*, final: str = "") -> tuple[bool, str]:
    with _lock:
        _state["end_seq"] = int(_state["end_seq"]) + 1
        _state["recording"] = False
        if final:
            _state["final"] = str(final).strip()
        _touch()
        seq = int(_state["end_seq"])
    return True, f"PTT end (seq={seq})"


def ptt_tap() -> tuple[bool, str, PttAction]:
    """One global UI button: wake -> begin -> end -> begin -> ..."""
    with _lock:
        if not _state["awake"]:
            _state["awake"] = True
            _state["wake_seq"] = int(_state["wake_seq"]) + 1
            _state["recording"] = False
            _state["partial"] = ""
            _state["final"] = ""
            _touch()
            return True, "awake, tap again to speak", "wake"
        if _state["recording"]:
            _state["end_seq"] = int(_state["end_seq"]) + 1
            _state["recording"] = False
            _touch()
            return True, "recording ended, recognizing", "end"
        _state["begin_seq"] = int(_state["begin_seq"]) + 1
        _state["recording"] = True
        _state["partial"] = ""
        _state["final"] = ""
        _touch()
        return True, "recording, speak now", "begin"


def ptt_set_partial(text: str) -> tuple[bool, str]:
    with _lock:
        _state["partial"] = str(text or "")
        _touch()
    return True, "ok"


def ptt_set_final(text: str) -> tuple[bool, str]:
    with _lock:
        _state["final"] = str(text or "").strip()
        _state["partial"] = _state["final"]
        _touch()
    return True, "ok"


def ptt_status() -> dict[str, Any]:
    with _lock:
        return {
            "awake": bool(_state["awake"]),
            "wake_seq": int(_state["wake_seq"]),
            "begin_seq": int(_state["begin_seq"]),
            "end_seq": int(_state["end_seq"]),
            "recording": bool(_state["recording"]),
            "partial": str(_state["partial"]),
            "final": str(_state["final"]),
        }
