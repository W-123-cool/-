"""巡逻模式单例服务 + 后台 tick。"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from patrol_mode.config import TICK_INTERVAL_SEC
from patrol_mode.engine import PatrolEngine
from patrol_mode.models import ScheduleEntry
from patrol_mode.patrol_executor import get_patrol_executor
from patrol_mode.scheduler import load_schedules, save_schedules

_engine = PatrolEngine()
_tick_thread: Optional[threading.Thread] = None
_tick_stop = threading.Event()
_LOG = logging.getLogger("patrol.service")


def get_patrol_service() -> PatrolEngine:
    return _engine


def _on_patrol_waypoint_mqtt(data: dict[str, Any]) -> None:
    mt = str(data.get("msg_type", "")).strip()
    _LOG.info(
        "MQTT patrol feedback: %s req=%s label=%s reason=%s",
        mt,
        data.get("request_id", ""),
        data.get("label", ""),
        data.get("reason", ""),
    )
    ex = get_patrol_executor()
    if mt == "patrol_waypoint_done":
        ex.on_waypoint_done(data)
    elif mt == "patrol_waypoint_failed":
        ex.on_waypoint_failed(data)


def _on_security_event_mqtt(data: dict[str, Any]) -> None:
    mt = str(data.get("msg_type", "")).strip()
    _LOG.info("MQTT security event: %s", mt)
    eng = get_patrol_service()
    if mt == "security_person_event":
        eng.on_security_person_event(data)
    elif mt == "patrol_track_status":
        eng.on_patrol_track_status(data)
    elif mt == "guard_status":
        eng.on_guard_status(data)


def init_patrol_integrations() -> None:
    get_patrol_executor().set_round_complete_callback(_engine.on_patrol_round_complete)
    try:
        from mqtt_robot_bridge import bridge_enabled, get_bridge

        if bridge_enabled():
            br = get_bridge()
            br.set_patrol_waypoint_handler(_on_patrol_waypoint_mqtt)
            br.set_security_event_handler(_on_security_event_mqtt)
            _engine.sync_vision_to_vehicle()
    except Exception:
        pass


def start_patrol_tick_loop() -> None:
    global _tick_thread
    init_patrol_integrations()
    if _tick_thread and _tick_thread.is_alive():
        return
    _tick_stop.clear()

    def _loop() -> None:
        while not _tick_stop.is_set():
            try:
                _engine.tick()
                _publish_master_status()
            except Exception:
                pass
            _tick_stop.wait(TICK_INTERVAL_SEC)

    _tick_thread = threading.Thread(target=_loop, daemon=True, name="patrol_tick")
    _tick_thread.start()


def stop_patrol_tick_loop() -> None:
    _tick_stop.set()


def publish_patrol_master_status_now() -> None:
    """立即向车端广播 master 快照（退出巡逻时避免最多 1s 滞后）。"""
    _publish_master_status()


def _publish_master_status() -> None:
    try:
        from mqtt_robot_bridge import bridge_enabled, get_bridge

        if not bridge_enabled():
            return
        st = _engine.status_dict()
        vision = st.get("vision_settings") or {}
        get_bridge().publish_patrol_master_status(
            {
                "master_mode": st.get("master_mode", "idle"),
                "security_active": st.get("security_active", False),
                "patrol_sub_state": st.get("sub_state", ""),
                "patrol_epoch": st.get("patrol_epoch", 0),
                "track_phase": st.get("track_phase", "idle"),
                "guard_view_track_enabled": vision.get("guard_view_track_enabled", True),
                "patrol_track_enabled": vision.get("patrol_track_enabled", True),
                "vision_conf": vision.get("detection_conf", 0.30),
            }
        )
    except Exception:
        pass


def list_schedules() -> list[dict[str, Any]]:
    return [e.to_dict() for e in load_schedules()]


def replace_schedules(items: list[dict[str, Any]]) -> None:
    entries = [ScheduleEntry.from_dict(x) for x in items if isinstance(x, dict)]
    save_schedules(entries)
