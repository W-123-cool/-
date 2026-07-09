# -*- coding: utf-8 -*-
"""User trigger for push-to-talk (terminal Enter now; UI long-press later)."""
from __future__ import annotations

import os
import sys
from typing import Protocol


class InputTrigger(Protocol):
    def wait_trigger(self, *, hint: str) -> bool:
        """Return True when user wants to start recording; False to quit."""


class TerminalEnterTrigger:
    """Wait for Enter on stdin (future UI replaces this implementation)."""

    def wait_trigger(self, *, hint: str) -> bool:
        print(hint, flush=True)
        try:
            line = input()
        except EOFError:
            return False
        if line.strip().lower() in ("q", "quit", "exit"):
            return False
        return True

    def wait_finish(self, *, hint: str) -> bool:
        """Second Enter: end recording and upload."""
        print(hint, flush=True)
        try:
            line = input()
        except EOFError:
            return False
        if line.strip().lower() in ("q", "quit", "exit"):
            return False
        return True


def make_input_trigger() -> InputTrigger:
    mode = os.environ.get("VOICE_INPUT_MODE", "enter").strip().lower()
    if mode in ("enter", "terminal", ""):
        return TerminalEnterTrigger()
    if mode == "ui":
        raise NotImplementedError(
            "VOICE_INPUT_MODE=ui not implemented; use enter or UiPushToTalkTrigger"
        )
    return TerminalEnterTrigger()
