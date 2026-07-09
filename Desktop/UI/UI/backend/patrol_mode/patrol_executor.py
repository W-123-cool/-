"""PC 端巡逻路线顺序下发与进度（P1b）。"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Optional

from patrol_mode.config import ENTRY_ROOM_ID, MOCK_WAYPOINT_SEC, PATROL_WAYPOINT_TIMEOUT_SEC, mock_vehicle_enabled
from patrol_mode.map_sync import check_map_sync
from patrol_mode.plan_service import load_selected_plan, ordered_waypoints, save_selected_plan
from patrol_mode.models import PatrolSubState

_LOG = logging.getLogger("patrol.executor")
_TRANSIENT_FAIL_RE = re.compile(
    r"nav2 not ready|nav bridge not ready|navigation failed|action server",
    re.I,
)
_WAYPOINT_RETRY_MAX = int(os.environ.get("PATROL_WAYPOINT_RETRY_MAX", "5"))
_WAYPOINT_RETRY_DELAY_SEC = float(os.environ.get("PATROL_WAYPOINT_RETRY_DELAY_SEC", "8"))


class PatrolRouteExecutor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._plan: Optional[dict[str, Any]] = None
        self._route: list[dict[str, Any]] = []
        self._route_pos = 0
        self._active = False
        self._waiting = False
        self._wait_deadline = 0.0
        self._current_req = ""
        self._mock_deadline = 0.0
        self._patrol_epoch = 0
        self._on_round_complete: Optional[Callable[[], None]] = None
        self._last_error = ""
        self._waypoint_retry_count = 0
        self._paused_for_track = False

    def set_round_complete_callback(self, fn: Callable[[], None]) -> None:
        self._on_round_complete = fn

    def progress_dict(self) -> dict[str, Any]:
        with self._lock:
            wp = self._route[self._route_pos] if self._route and self._route_pos < len(self._route) else None
            return {
                "active": self._active,
                "waiting_vehicle": self._waiting,
                "route_pos": self._route_pos,
                "route_len": len(self._route),
                "current_label": (wp or {}).get("label", ""),
                "current_index": (wp or {}).get("index"),
                "plan_path": (self._plan or {}).get("_path", ""),
                "plan_id": (self._plan or {}).get("_selected_id", ""),
                "last_error": self._last_error,
                "retry_count": self._waypoint_retry_count,
                "paused_for_track": self._paused_for_track,
            }

    def select_plan(self, plan_path: str, plan_id: str = "") -> tuple[bool, str, dict[str, Any]]:
        try:
            plan = save_selected_plan(plan_path, plan_id)
        except Exception as e:
            return False, str(e), {}
        ok, msg, detail = check_map_sync(plan)
        if not ok:
            return False, msg, detail
        with self._lock:
            self._plan = plan
            self._route = ordered_waypoints(plan)
            self._route_pos = 0
            self._last_error = ""
        return True, msg, {"plan": plan.get("_selected_id"), "waypoints": len(self._route), **detail}

    def start_round(self, patrol_epoch: int) -> tuple[bool, str]:
        with self._lock:
            plan = self._plan or load_selected_plan()
            if not plan:
                return False, "未选择巡逻计划，请先在 Web 选择 patrol JSON"
            ok, msg, _ = check_map_sync(plan)
            if not ok:
                return False, msg
            self._plan = plan
            self._route = ordered_waypoints(plan)
            self._route_pos = 0
            self._active = True
            self._waiting = False
            self._patrol_epoch = patrol_epoch
            self._last_error = ""
            self._waypoint_retry_count = 0
        return self._dispatch_current()

    def stop(self) -> None:
        with self._lock:
            self._active = False
            self._waiting = False
            self._mock_deadline = 0.0
            self._wait_deadline = 0.0
            self._paused_for_track = False

    def pause_for_track(self) -> None:
        with self._lock:
            self._waiting = False
            self._mock_deadline = 0.0
            self._wait_deadline = 0.0
            self._paused_for_track = True

    def resume_after_track(self, patrol_epoch: int) -> tuple[bool, str]:
        with self._lock:
            if not self._route:
                return False, "无路线"
            self._paused_for_track = False
            self._active = True
            self._patrol_epoch = patrol_epoch
        return self._dispatch_current()

    def dispatch_waypoint_by_index(self, index: int, patrol_epoch: int) -> tuple[bool, str]:
        with self._lock:
            if not self._route:
                return False, "无路线"
            pos = next((i for i, w in enumerate(self._route) if int(w.get("index", -1)) == int(index)), None)
            if pos is None:
                return False, f"路线中无 index={index}"
            self._route_pos = pos
            self._paused_for_track = False
            self._active = True
            self._waiting = False
            self._patrol_epoch = patrol_epoch
            self._waypoint_retry_count = 0
        return self._dispatch_current()

    def on_waypoint_done(self, data: dict[str, Any]) -> None:
        req = str(data.get("request_id", "") or "")
        label = str(data.get("label", "") or "")
        with self._lock:
            if not self._active:
                _LOG.warning("patrol_waypoint_done ignored (route inactive) req=%s label=%s", req, label)
                return
            if req != self._current_req:
                _LOG.warning(
                    "patrol_waypoint_done ignored (request_id mismatch) got=%s expect=%s label=%s",
                    req,
                    self._current_req,
                    label,
                )
                return
            self._waiting = False
            self._waypoint_retry_count = 0
            self._route_pos += 1
            _LOG.info("patrol_waypoint_done accepted label=%s route_pos=%s", label, self._route_pos)
            if self._route_pos >= len(self._route):
                self._active = False
                cb = self._on_round_complete
            else:
                cb = None
        if cb:
            cb()
            return
        self._dispatch_current()

    def on_waypoint_failed(self, data: dict[str, Any]) -> None:
        req = str(data.get("request_id", "") or "")
        reason = str(data.get("reason", "failed"))
        with self._lock:
            if req and req != self._current_req:
                _LOG.warning(
                    "patrol_waypoint_failed ignored (request_id mismatch) got=%s expect=%s reason=%s",
                    req,
                    self._current_req,
                    reason,
                )
                return
            self._waiting = False
            if _TRANSIENT_FAIL_RE.search(reason) and self._waypoint_retry_count < _WAYPOINT_RETRY_MAX:
                self._waypoint_retry_count += 1
                self._last_error = f"{reason}（重试 {self._waypoint_retry_count}/{_WAYPOINT_RETRY_MAX}）"
                retry_n = self._waypoint_retry_count
                _LOG.warning("patrol waypoint transient fail, retry %s: %s", retry_n, reason)
                threading.Timer(_WAYPOINT_RETRY_DELAY_SEC, self._dispatch_current).start()
                return
            self._active = False
            self._last_error = reason
            _LOG.error("patrol waypoint failed, route stopped: %s", reason)

    def tick(self) -> None:
        with self._lock:
            if not self._active or not self._waiting:
                return
            if mock_vehicle_enabled():
                if self._mock_deadline and time.monotonic() >= self._mock_deadline:
                    self._waiting = False
                    self._route_pos += 1
                    done = self._route_pos >= len(self._route)
                    if done:
                        self._active = False
                        cb = self._on_round_complete
                    else:
                        cb = None
                    if cb:
                        threading.Thread(target=cb, daemon=True).start()
                    elif not done:
                        threading.Thread(target=self._dispatch_current, daemon=True).start()
                return
            if self._wait_deadline and time.monotonic() > self._wait_deadline:
                self._waiting = False
                self._active = False
                self._last_error = "等待车端 waypoint 超时"

    def _skip_anchor_if_already_home(self) -> None:
        """已在起点时跳过路线首段锚点 100，避免多余导航占用车端。"""
        if mock_vehicle_enabled():
            return
        while True:
            with self._lock:
                if not self._active or not self._route or self._route_pos >= len(self._route):
                    return
                wp = self._route[self._route_pos]
                label = str(wp.get("label", "") or wp.get("id", ""))
                if label != ENTRY_ROOM_ID:
                    return
            try:
                from patrol_mode.nav_helpers import robot_at_home

                if not robot_at_home():
                    return
            except Exception:
                return
            with self._lock:
                if not self._active or self._route_pos >= len(self._route):
                    return
                wp = self._route[self._route_pos]
                label = str(wp.get("label", "") or wp.get("id", ""))
                if label != ENTRY_ROOM_ID:
                    return
                self._route_pos += 1
                _LOG.info("skip anchor %s at home, route_pos=%s", label, self._route_pos)
                if self._route_pos >= len(self._route):
                    self._active = False
                    cb = self._on_round_complete
                else:
                    cb = None
            if cb:
                cb()
                return

    def _dispatch_current(self) -> tuple[bool, str]:
        self._skip_anchor_if_already_home()
        with self._lock:
            if self._paused_for_track:
                return False, "TRACK 暂停中"
            if not self._active:
                return False, "路线未激活"
            if not self._route or self._route_pos >= len(self._route):
                return False, "路线为空"
            wp = self._route[self._route_pos]
            req = uuid.uuid4().hex[:12]
            self._current_req = req
            self._waiting = True
            epoch = self._patrol_epoch
            plan = dict(self._plan or {})

        if mock_vehicle_enabled():
            with self._lock:
                self._mock_deadline = time.monotonic() + MOCK_WAYPOINT_SEC
            return True, f"mock 下发 {wp.get('label', wp.get('index'))}"

        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if not bridge_enabled():
                with self._lock:
                    self._waiting = False
                    self._active = False
                    self._last_error = "MQTT 未启用"
                return False, "MQTT 未启用，无法下发巡逻点"
            get_bridge().publish_patrol_nav_waypoint(
                request_id=req,
                patrol_epoch=epoch,
                floor=str(plan.get("floor") or wp.get("floor") or "1F"),
                index=int(wp.get("index", 0)),
                label=str(wp.get("label", "")),
                x=float(wp["x"]),
                y=float(wp["y"]),
                yaw=float(wp.get("yaw", 0.0)),
                action=str(wp.get("action", "nav_only")),
                map_yaml=str(plan.get("map_yaml", "")),
            )
            with self._lock:
                self._wait_deadline = time.monotonic() + PATROL_WAYPOINT_TIMEOUT_SEC
            _LOG.info(
                "patrol_nav_waypoint published label=%s index=%s req=%s",
                wp.get("label", wp.get("index")),
                wp.get("index"),
                req,
            )
            return True, f"已下发 {wp.get('label', wp.get('index'))}"
        except Exception as e:
            with self._lock:
                self._waiting = False
                self._active = False
                self._last_error = str(e)
            return False, str(e)


_executor = PatrolRouteExecutor()


def get_patrol_executor() -> PatrolRouteExecutor:
    return _executor
