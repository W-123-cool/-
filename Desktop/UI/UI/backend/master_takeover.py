"""总控遥控接管：进入时快照；释放时按导览/送货规则恢复（P4）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from state_machine import RobotState
from task_manager import robot_sm
from tour_phases import ACTIVE_PHASES, PHASE_NAVIGATING, normalize_phase


@dataclass
class MasterTakeoverSnapshot:
    robot_state: str = "idle"
    tour_phase: str = "idle"
    tour_active: bool = False
    tour_id: str = ""
    tour_room: str = ""
    was_tour: bool = False
    delivery_goal_room: str = ""
    was_delivering: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MasterTakeoverSnapshot:
        return cls(
            robot_state=str(data.get("robot_state", "idle")),
            tour_phase=str(data.get("tour_phase", "idle")),
            tour_active=bool(data.get("tour_active")),
            tour_id=str(data.get("tour_id", "")),
            tour_room=str(data.get("tour_room", "")),
            was_tour=bool(data.get("was_tour")),
            delivery_goal_room=str(data.get("delivery_goal_room", data.get("goal_room", ""))),
            was_delivering=bool(data.get("was_delivering")),
        )


_lock_snapshot: Optional[MasterTakeoverSnapshot] = None


def _mqtt_on() -> bool:
    from mqtt_robot_bridge import bridge_enabled

    return bridge_enabled()


def _cancel_vehicle_nav(reason: str = "master_takeover") -> None:
    if not _mqtt_on():
        return
    from mqtt_robot_bridge import get_bridge

    get_bridge().publish_nav_cancel(reason)


def capture_snapshot() -> MasterTakeoverSnapshot:
    from tour_manager import get_tour_status

    st = get_tour_status()
    phase = normalize_phase(str(st.get("phase", "idle")))
    return MasterTakeoverSnapshot(
        robot_state=robot_sm.state.value,
        tour_phase=phase,
        tour_active=bool(st.get("active")),
        tour_id=str(st.get("tour_id", "") or ""),
        tour_room=str(st.get("room", "") or ""),
        was_tour=phase in ACTIVE_PHASES,
    )


def on_master_takeover_enter() -> tuple[bool, str, dict[str, Any]]:
    """总控进入遥控：取消当前导航，导览进原地待机并冻结超时。"""
    global _lock_snapshot
    from tour_manager import enter_holding_from_nav_stop, pause_tour_deadlines

    snap = capture_snapshot()
    _lock_snapshot = snap

    if snap.tour_active and snap.tour_phase == PHASE_NAVIGATING:
        enter_holding_from_nav_stop(message="总控接管，已截停导览")
    elif snap.tour_active:
        pause_tour_deadlines()
    else:
        _cancel_vehicle_nav("master_takeover")

    return True, "总控接管已记录", snap.to_dict()


def on_master_takeover_release(
    payload: Optional[dict[str, Any]] = None,
) -> tuple[bool, str, dict[str, Any]]:
    """总控释放遥控：导览→返回起点；送货 idle/待投→返回起点；其它送货态由车端/任务队列恢复。"""
    global _lock_snapshot
    from tour_manager import begin_return_home, resume_tour_deadlines

    snap = _lock_snapshot or MasterTakeoverSnapshot()
    if payload:
        ext = MasterTakeoverSnapshot.from_dict(payload)
        if ext.was_tour:
            snap.was_tour = True
        if ext.was_delivering:
            snap.was_delivering = True
            snap.robot_state = RobotState.DELIVERING.value
        elif ext.robot_state in (
            RobotState.IDLE.value,
            RobotState.PENDING_DELIVERY.value,
            RobotState.DELIVERING.value,
            RobotState.AWAIT_PICKUP.value,
        ):
            snap.robot_state = ext.robot_state
        if ext.delivery_goal_room:
            snap.delivery_goal_room = ext.delivery_goal_room

    _lock_snapshot = None
    resume_tour_deadlines()

    action = "idle"
    msg = "已释放总控"

    if snap.was_tour or snap.tour_phase in ACTIVE_PHASES:
        ok, m = begin_return_home(reason="master_release")
        if not ok:
            return False, m, {"action": "tour_return_failed"}
        action = "tour_returning"
        msg = m
    elif snap.robot_state in (RobotState.IDLE.value, RobotState.PENDING_DELIVERY.value):
        if robot_sm.on_tour_end_begin_return():
            if _mqtt_on():
                from mqtt_robot_bridge import get_bridge
                from vehicle_rooms import ENTRY_ROOM_ID

                get_bridge().publish_nav_room(ENTRY_ROOM_ID)
            action = "returning"
            msg = "已进入返回起点"
    elif snap.robot_state in (
        RobotState.DELIVERING.value,
        RobotState.AWAIT_PICKUP.value,
    ):
        action = "resume_delivery"
        goal = snap.delivery_goal_room.strip()
        msg = "送货状态保持，恢复送货导航"
        if _mqtt_on() and goal:
            from mqtt_robot_bridge import get_bridge

            get_bridge().publish_nav_room(goal)

    return True, msg, {"action": action, "snapshot": snap.to_dict()}
