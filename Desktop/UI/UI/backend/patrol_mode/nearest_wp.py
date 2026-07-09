"""Nearest patrol waypoint from robot pose (P1c map blink)."""
from __future__ import annotations

import math
from typing import Any, Optional

from patrol_mode.plan_service import load_selected_plan, ordered_waypoints


def nearest_waypoint(
    pose_x: float,
    pose_y: float,
    *,
    plan: Optional[dict[str, Any]] = None,
    floor: str = "",
) -> Optional[dict[str, Any]]:
    plan = plan or load_selected_plan()
    if not plan:
        return None
    wps = ordered_waypoints(plan)
    if floor:
        wps = [w for w in wps if not w.get("floor") or str(w.get("floor")) == floor]
    if not wps:
        return None
    best: Optional[dict[str, Any]] = None
    best_d = float("inf")
    for wp in wps:
        try:
            dx = float(wp["x"]) - pose_x
            dy = float(wp["y"]) - pose_y
        except (KeyError, TypeError, ValueError):
            continue
        d = math.hypot(dx, dy)
        if d < best_d:
            best_d = d
            best = dict(wp)
            best["distance_m"] = round(d, 3)
    return best
