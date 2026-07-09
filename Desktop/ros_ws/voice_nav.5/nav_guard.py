# -*- coding: utf-8 -*-
"""Detect active tour/delivery navigation from switcher heartbeat."""
from __future__ import annotations

ACTIVE_MISSION_STATES = frozenset(
    {
        "NAVIGATING_TO_ROOM",
        "GOING_TO_ELEVATOR",
        "WAITING_ELEVATOR",
        "SWITCHING_MAP",
    }
)


def is_active_mission(nav_state: str) -> bool:
    return (nav_state or "").strip() in ACTIVE_MISSION_STATES
