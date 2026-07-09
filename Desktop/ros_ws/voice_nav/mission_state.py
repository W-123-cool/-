# -*- coding: utf-8 -*-
"""Unified navigation mission state for voice agent."""
from __future__ import annotations

import os
import threading
import time
from enum import Enum
from typing import Callable, Optional

from . import nav_guard


class MissionPhase(str, Enum):
    IDLE = "idle"
    NAVIGATING = "navigating"
    ARRIVED = "arrived"
    CANCELLED = "cancelled"
    FAILED = "failed"


def _watchdog_sec() -> float:
    try:
        return float(os.environ.get("VOICE_NAV_MISSION_WATCHDOG_SEC", "90"))
    except ValueError:
        return 90.0


class NavSession:
    """Single source of truth for voice-side navigation mission state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._phase = MissionPhase.IDLE
        self._goal_room_id: Optional[str] = None
        self._mission_nav_epoch: Optional[int] = None
        self._nav_started_at = 0.0
        self._last_active_hb = 0.0
        self._last_nav_state = ""
        self._status_spoken: set[str] = set()
        self._on_mission_end: list[Callable[[MissionPhase], None]] = []

    def add_mission_end_callback(self, fn: Callable[[MissionPhase], None]) -> None:
        self._on_mission_end.append(fn)

    @property
    def phase(self) -> MissionPhase:
        with self._lock:
            return self._phase

    @property
    def goal_room_id(self) -> Optional[str]:
        with self._lock:
            return self._goal_room_id

    @property
    def mission_nav_epoch(self) -> Optional[int]:
        with self._lock:
            return self._mission_nav_epoch

    @property
    def last_nav_state(self) -> str:
        with self._lock:
            return self._last_nav_state

    def mission_active(self) -> bool:
        with self._lock:
            return self._phase == MissionPhase.NAVIGATING

    def mark_nav_started(self, room_id: str = "") -> None:
        rid = str(room_id or "").strip()
        with self._lock:
            self._phase = MissionPhase.NAVIGATING
            if rid:
                self._goal_room_id = rid
            self._mission_nav_epoch = None
            now = time.monotonic()
            self._nav_started_at = now
            self._last_active_hb = now
            self._status_spoken.clear()

    def confirm_nav_epoch(self, nav_epoch: int, goal_room: str = "") -> None:
        epoch = int(nav_epoch)
        room = str(goal_room or "").strip()
        with self._lock:
            if self._phase != MissionPhase.NAVIGATING:
                return
            if room and self._goal_room_id and room != self._goal_room_id:
                return
            self._mission_nav_epoch = epoch

    def _end_mission(self, phase: MissionPhase) -> None:
        callbacks = list(self._on_mission_end)
        self._phase = phase
        if phase in (MissionPhase.IDLE, MissionPhase.ARRIVED, MissionPhase.CANCELLED, MissionPhase.FAILED):
            self._goal_room_id = None
            self._mission_nav_epoch = None
        for fn in callbacks:
            try:
                fn(phase)
            except Exception:
                pass

    def mark_nav_ended(self, phase: MissionPhase = MissionPhase.IDLE) -> None:
        with self._lock:
            if self._phase != MissionPhase.NAVIGATING:
                if phase == MissionPhase.IDLE:
                    self._goal_room_id = None
                    self._mission_nav_epoch = None
                return
            self._end_mission(phase)

    def on_heartbeat(self, nav_state: str) -> tuple[str, str, bool]:
        """Update from MQTT heartbeat. Returns (prev, current, became_idle)."""
        st = (nav_state or "").strip()
        with self._lock:
            prev = self._last_nav_state
            self._last_nav_state = st
            became_idle = False
            if nav_guard.is_active_mission(st):
                self._last_active_hb = time.monotonic()
            elif (
                prev
                and nav_guard.is_active_mission(prev)
                and not nav_guard.is_active_mission(st)
                and self._phase == MissionPhase.NAVIGATING
                and self._mission_nav_epoch is not None
            ):
                became_idle = True
                self._end_mission(MissionPhase.IDLE)
            return prev, st, became_idle

    def on_tour_arrived(self, room: str, nav_epoch: Optional[int] = None) -> Optional[str]:
        rid = str(room or "").strip()
        with self._lock:
            goal = self._goal_room_id
            if goal and rid and rid != goal:
                return None
            if self._mission_nav_epoch is not None and nav_epoch is not None:
                if int(nav_epoch) != self._mission_nav_epoch:
                    return None
            if rid and not goal:
                self._goal_room_id = rid
            if self._phase == MissionPhase.NAVIGATING:
                self._end_mission(MissionPhase.ARRIVED)
            return self._goal_room_id or rid or None

    def on_nav_cancel(self) -> None:
        with self._lock:
            if self._phase == MissionPhase.NAVIGATING:
                self._end_mission(MissionPhase.CANCELLED)

    def should_speak_status(self, cache_key: str) -> bool:
        if os.environ.get("VOICE_NAV_TTS_STATUS_DEDUP", "1").strip().lower() in ("0", "false", "no"):
            return True
        with self._lock:
            if cache_key in self._status_spoken:
                return False
            self._status_spoken.add(cache_key)
            return True

    def tick_watchdog(self, nav_state: str) -> bool:
        """Force end mission if navigating too long without active heartbeat."""
        if not self.mission_active():
            return False
        st = (nav_state or "").strip()
        if nav_guard.is_active_mission(st):
            return False
        if st and st != "IDLE":
            return False
        timeout = _watchdog_sec()
        now = time.monotonic()
        with self._lock:
            if self._phase != MissionPhase.NAVIGATING:
                return False
            if now - self._last_active_hb < timeout:
                return False
            if self._nav_started_at and now - self._nav_started_at < timeout:
                return False
            self._end_mission(MissionPhase.IDLE)
            return True
