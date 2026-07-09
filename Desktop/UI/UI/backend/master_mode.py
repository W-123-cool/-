# -*- coding: utf-8 -*-
"""安防/巡逻模式：PC 本地状态 + MQTT master 状态。"""
from __future__ import annotations

from typing import Any, Optional


def patrol_security_active() -> tuple[bool, str]:
    try:
        from patrol_mode.service import get_patrol_service

        svc = get_patrol_service()
        if svc.security_active():
            st = svc.status_dict()
            sub = st.get("sub_state", "")
            return True, f"巡逻模式（{sub}），送货/导览暂不可用"
    except Exception:
        pass
    return False, ""


def master_mode_from_snapshot(snap: dict[str, Any]) -> str:
    active, _ = patrol_security_active()
    if active:
        try:
            from patrol_mode.service import get_patrol_service

            return get_patrol_service().master_mode_label()
        except Exception:
            return "patrol"
    mm = snap.get("master_mode")
    if mm:
        return str(mm).strip().lower()
    m = snap.get("master") or {}
    if isinstance(m, dict) and m.get("master_mode"):
        return str(m["master_mode"]).strip().lower()
    return "idle"


def security_active_from_snapshot(snap: dict[str, Any]) -> bool:
    active, _ = patrol_security_active()
    if active:
        return True
    if snap.get("security_active") is True:
        return True
    m = snap.get("master") or {}
    if isinstance(m, dict) and m.get("security_active") is True:
        return True
    mm = master_mode_from_snapshot(snap)
    return mm in ("patrol", "guard", "manual_takeover")


def security_blocks_business(snap: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
    blocked, reason = patrol_security_active()
    if blocked:
        return True, reason

    if snap is None:
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if not bridge_enabled():
                return False, ""
            snap = get_bridge().snapshot()
        except Exception:
            return False, ""
    if not snap:
        return False, ""
    if security_active_from_snapshot(snap):
        mm = master_mode_from_snapshot(snap)
        return True, f"总控安防模式（{mm}），送货/导览暂不可用"
    return False, ""
