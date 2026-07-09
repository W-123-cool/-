# -*- coding: utf-8 -*-
"""User trigger for push-to-talk (terminal Enter or UI via backend PTT API)."""
from __future__ import annotations

import os
import time
from typing import Any

from .tour_api_client import TourApiClient


class InputTrigger:
    def wait_trigger(self, *, hint: str) -> bool:
        """Return True when user wants to start recording; False to quit."""

    def wait_finish(self, *, hint: str) -> bool:
        """Return True when recording should end and upload."""


class TerminalEnterTrigger(InputTrigger):
    """Wait for Enter on stdin."""

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
        print(hint, flush=True)
        try:
            line = input()
        except EOFError:
            return False
        if line.strip().lower() in ("q", "quit", "exit"):
            return False
        return True


class UiPushToTalkTrigger(InputTrigger):
    """Poll backend /api/tour/voice/ptt/* for UI wake / begin / end."""

    def __init__(self) -> None:
        self._api = TourApiClient.from_env()
        self._poll = float(os.environ.get("VOICE_UI_PTT_POLL_SEC", "0.12"))
        self._wake_seq = 0
        self._begin_seq = 0
        self._end_seq = 0
        self._sync_seq()

    def _sync_seq(self) -> None:
        try:
            st = self._api.ptt_status()
            self._wake_seq = int(st.get("wake_seq", 0) or 0)
            self._begin_seq = int(st.get("begin_seq", 0) or 0)
            self._end_seq = int(st.get("end_seq", 0) or 0)
        except Exception:
            pass

    def _status(self) -> dict[str, Any]:
        return self._api.ptt_status()

    def awake_sync(self) -> None:
        try:
            self._api.ptt_awake_sync()
        except Exception:
            pass

    def wait_wake(self, *, hint: str) -> bool:
        print(f"{hint}", flush=True)
        print("[UI-PTT] 等待主屏「语音输入」唤醒…", flush=True)
        self._sync_seq()
        while True:
            try:
                st = self._status()
                w = int(st.get("wake_seq", 0) or 0)
                if w > self._wake_seq:
                    self._wake_seq = w
                    print("[UI-PTT] 收到 UI 唤醒", flush=True)
                    return True
            except Exception as exc:
                print(f"[UI-PTT] poll error: {exc}", flush=True)
            time.sleep(self._poll)

    def wait_trigger(self, *, hint: str) -> bool:
        print(f"{hint}", flush=True)
        print("[UI-PTT] 等待主屏点击开始说话…", flush=True)
        self._sync_seq()
        while True:
            try:
                st = self._status()
                b = int(st.get("begin_seq", 0) or 0)
                if b > self._begin_seq or st.get("recording"):
                    self._begin_seq = max(b, self._begin_seq)
                    print("[UI-PTT] 收到开始信号，开麦", flush=True)
                    return True
            except Exception as exc:
                print(f"[UI-PTT] poll error: {exc}", flush=True)
            time.sleep(self._poll)

    def wait_finish(self, *, hint: str) -> bool:
        print(f"{hint}", flush=True)
        print("[UI-PTT] 录音中…请主屏再点「语音输入」结束", flush=True)
        last_partial = ""
        while True:
            try:
                st = self._status()
                partial = str(st.get("partial", "") or "")
                if partial and partial != last_partial:
                    last_partial = partial
                    print(f"\r... {partial}", end="", flush=True)
                e = int(st.get("end_seq", 0) or 0)
                if e > self._end_seq:
                    self._end_seq = e
                    print(flush=True)
                    print("[UI-PTT] 收到结束信号，上传分析", flush=True)
                    return True
            except Exception as exc:
                print(f"[UI-PTT] poll error: {exc}", flush=True)
            time.sleep(self._poll)


def ui_input_mode() -> bool:
    return os.environ.get("VOICE_INPUT_MODE", "").strip().lower() in (
        "ui",
        "screen",
        "onboard",
    )


def make_input_trigger() -> InputTrigger:
    if ui_input_mode():
        return UiPushToTalkTrigger()
    return TerminalEnterTrigger()


def publish_ptt_partial(text: str) -> None:
    if not ui_input_mode():
        return
    try:
        TourApiClient.from_env().ptt_set_partial(text)
    except Exception:
        pass


def publish_ptt_final(text: str) -> None:
    if not ui_input_mode():
        return
    try:
        TourApiClient.from_env().ptt_set_final(text)
    except Exception:
        pass


def publish_ptt_sleep() -> None:
    if not ui_input_mode():
        return
    try:
        TourApiClient.from_env().ptt_sleep()
    except Exception:
        pass


def publish_ptt_awake_sync() -> None:
    if not ui_input_mode():
        return
    try:
        TourApiClient.from_env().ptt_awake_sync()
    except Exception:
        pass
