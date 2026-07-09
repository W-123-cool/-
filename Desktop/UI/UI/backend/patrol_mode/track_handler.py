"""P1c person events and TRACK coordination."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from patrol_mode.config import (
    PERSON_EVENT_COOLDOWN_SEC,
    TRACK_GOAL_HZ,
    TRACK_MAX_LINEAR_MPS,
    TRACK_REENTRY_COOLDOWN_SEC,
)
from patrol_mode.models import PatrolSubState
from patrol_mode.nearest_wp import nearest_waypoint
from patrol_mode.patrol_executor import get_patrol_executor

_LOG = logging.getLogger("patrol.track")

_GUARD_STATES = (
    PatrolSubState.GUARD,
    PatrolSubState.GUARD_TIMER,
    PatrolSubState.GUARD_VIEW_TRACK,
)


class TrackCoordinator:
    def __init__(self) -> None:
        self._last_person_mono = 0.0
        self._track_request_id = ""
        self._track_reentry_block_until = 0.0

    def on_security_person_event(self, engine: Any, data: dict[str, Any]) -> None:
        if not engine.security_active():
            return
        now = time.monotonic()
        if now < self._track_reentry_block_until:
            _LOG.debug(
                "person_event ignored (track re-entry cooldown %.1fs left)",
                self._track_reentry_block_until - now,
            )
            return
        if now - self._last_person_mono < PERSON_EVENT_COOLDOWN_SEC:
            return
        self._last_person_mono = now

        sub = engine.state.sub_state
        conf = float(data.get("confidence", 0) or 0)
        _LOG.info("person_event sub=%s conf=%.2f", sub.value, conf)

        pose_x = data.get("pose_x")
        pose_y = data.get("pose_y")
        near = None
        if pose_x is not None and pose_y is not None:
            near = nearest_waypoint(float(pose_x), float(pose_y), floor=str(data.get("floor", "")))
            if near:
                engine.state.nearest_wp_index = near.get("index")
                engine.state.nearest_wp_label = str(near.get("label", ""))

        # GUARD：告警 + 可选车端视角跟踪；不进入 Nav2 PATROL TRACK
        if sub in _GUARD_STATES:
            engine.state.message = f"GUARD 识人告警 conf={conf:.2f}（视角跟踪由车端执行）"
            return

        # PATROL / spin：进入 Nav2 TRACK
        if sub != PatrolSubState.PATROL:
            return
        if engine.state.sub_state == PatrolSubState.TRACK:
            return

        ex = get_patrol_executor()
        prog = ex.progress_dict()
        resume_index = prog.get("current_index")
        resume_label = str(prog.get("current_label", ""))
        if resume_index is None:
            _LOG.warning("person_event in PATROL but no current_index")
            return

        ex.pause_for_track()
        engine.state.sub_state = PatrolSubState.TRACK
        engine.state.track_phase = "following"
        engine.state.track_resume_index = int(resume_index)
        engine.state.track_resume_label = resume_label
        engine.state.message = f"巡逻识人，进入 TRACK，resume={resume_label}"

        self._publish_track_start(engine, resume_index, resume_label)

    def on_patrol_track_status(self, engine: Any, data: dict[str, Any]) -> None:
        if engine.state.sub_state != PatrolSubState.TRACK:
            return
        phase = str(data.get("phase", "") or "").strip().lower()
        engine.state.track_phase = phase or engine.state.track_phase
        if phase == "scan_360":
            engine.state.message = "TRACK：目标丢失，360° 扫描"
        elif phase == "following":
            engine.state.message = "TRACK：跟随中"
        elif phase == "lost_confirmed":
            engine.state.message = "TRACK：确认丢失，回巡逻点续巡"
            self._resume_patrol(engine)

    def _publish_track_start(self, engine: Any, resume_index: int, resume_label: str) -> None:
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if not bridge_enabled():
                return
            br = get_bridge()
            req = uuid.uuid4().hex[:12]
            self._track_request_id = req
            br.publish_patrol_motion_mode("track_nav")
            br.publish_patrol_track_start(
                request_id=req,
                patrol_epoch=engine.state.patrol_epoch,
                resume_index=resume_index,
                resume_label=resume_label,
                max_linear_mps=TRACK_MAX_LINEAR_MPS,
                goal_update_hz=TRACK_GOAL_HZ,
            )
        except Exception as e:
            _LOG.error("publish track_start failed: %s", e)

    def _resume_patrol(self, engine: Any) -> None:
        ex = get_patrol_executor()
        idx = engine.state.track_resume_index
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if bridge_enabled():
                br = get_bridge()
                br.publish_patrol_track_stop("resume_patrol")
                br.publish_patrol_motion_mode("nav")
                if idx is not None:
                    ok, msg = ex.dispatch_waypoint_by_index(int(idx), engine.state.patrol_epoch)
                    engine.state.message = msg if ok else f"续巡失败: {msg}"
                else:
                    ok, msg = ex.resume_after_track(engine.state.patrol_epoch)
                    engine.state.message = msg if ok else f"续巡失败: {msg}"
        except Exception as e:
            engine.state.message = f"续巡异常: {e}"
        engine.state.sub_state = PatrolSubState.PATROL
        engine.state.track_phase = "idle"
        engine.state.track_resume_index = None
        engine.state.track_resume_label = ""
        self._track_reentry_block_until = time.monotonic() + TRACK_REENTRY_COOLDOWN_SEC
        _LOG.info("track re-entry blocked for %.0fs after resume", TRACK_REENTRY_COOLDOWN_SEC)

    def stop_track(self, reason: str = "exit_security") -> None:
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if bridge_enabled():
                br = get_bridge()
                # 必须通知车端 track_assist 停转（含 360° 扫描），再切 motion idle
                br.publish_patrol_track_stop(reason)
                br.publish_patrol_motion_mode("idle")
        except Exception:
            pass
        self._track_request_id = ""
        if reason == "exit_security":
            self._track_reentry_block_until = 0.0

    def publish_motion_for_substate(self, sub_state: PatrolSubState) -> None:
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if not bridge_enabled():
                return
            br = get_bridge()
            if sub_state in (PatrolSubState.GUARD, PatrolSubState.GUARD_TIMER):
                br.publish_patrol_motion_mode("guard_idle")
            elif sub_state == PatrolSubState.GUARD_VIEW_TRACK:
                br.publish_patrol_motion_mode("guard_view_track")
            elif sub_state == PatrolSubState.PATROL:
                br.publish_patrol_motion_mode("nav")
            elif sub_state == PatrolSubState.TRACK:
                br.publish_patrol_motion_mode("track_nav")
            else:
                br.publish_patrol_motion_mode("idle")
        except Exception:
            pass


_coordinator = TrackCoordinator()


def get_track_coordinator() -> TrackCoordinator:
    return _coordinator
