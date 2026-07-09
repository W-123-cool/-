"""导航与抢占辅助。"""
from __future__ import annotations

from typing import Any, Optional

from patrol_mode.config import ENTRY_ROOM_ID
from patrol_mode.models import PatrolPreemptSnapshot, PatrolTaskConfig
from state_machine import RobotState
from task_manager import count_pending_delivery, robot_sm
from database import db_session


def _mqtt_on() -> bool:
    try:
        from mqtt_robot_bridge import bridge_enabled

        return bridge_enabled()
    except Exception:
        return False


def _bridge():
    from mqtt_robot_bridge import get_bridge

    return get_bridge()


def capture_preempt_snapshot() -> PatrolPreemptSnapshot:
    from tour_manager import get_tour_status

    tour = get_tour_status()
    rs = robot_sm.state
    snap = PatrolPreemptSnapshot(
        robot_state=rs.value,
        was_delivering=rs == RobotState.DELIVERING,
        was_await_pickup=rs == RobotState.AWAIT_PICKUP,
        tour_was_active=bool(tour.get("active")),
        tour_id=str(tour.get("tour_id", "") or ""),
    )
    if _mqtt_on():
        hb = _bridge().snapshot()
        snap.delivery_goal_room = str(hb.get("current_goal_room", "") or "")
        snap.task_request_id = str(hb.get("current_request_id", "") or "")
    return snap


def preempt_tour_and_delivery() -> list[str]:
    """进入巡逻模式：取消导览/送货导航并准备回起点。返回日志。"""
    logs: list[str] = []
    from tour_manager import begin_return_home, get_tour_status

    tour = get_tour_status()
    if tour.get("active"):
        ok, msg = begin_return_home(reason="patrol_mode_enter")
        logs.append(f"导览: {msg}" if ok else f"导览取消失败: {msg}")

    if _mqtt_on() and tour.get("active"):
        # 仅导览抢占需要显式 cancel；回 100 走 nav_room，switcher 内会 cancel 旧 Nav2 目标
        br = _bridge()
        br.publish_nav_cancel_legacy("patrol_mode_enter")
        logs.append("已发送 nav_cancel（导览抢占）")
    return logs


def nav_to_entry(reason: str = "patrol_return_prep") -> None:
    if not _mqtt_on():
        return
    br = _bridge()
    br.publish_patrol_motion_mode("nav")
    br.publish_nav_room_legacy(ENTRY_ROOM_ID)
    _ = reason


def nav_to_room(room: str) -> None:
    if not _mqtt_on() or not room.strip():
        return
    br = _bridge()
    br.publish_patrol_motion_mode("nav")
    br.publish_nav_room_legacy(room.strip())


def resume_after_patrol_exit(preempt: Optional[PatrolPreemptSnapshot]) -> tuple[str, list[str]]:
    """退出巡逻模式后的恢复逻辑。"""
    logs: list[str] = []
    if not preempt:
        nav_to_entry()
        logs.append("无快照：已下发回 100")
        return "已退出巡逻模式，回起点", logs

    if preempt.was_delivering or preempt.was_await_pickup:
        goal = preempt.delivery_goal_room.strip()
        if goal:
            nav_to_room(goal)
            logs.append(f"恢复送货导航 → {goal}")
            return f"已退出巡逻模式，恢复送货至 {goal}", logs
        logs.append("送货快照无 goal_room，仅 nav_cancel 后等待业务侧")
        return "已退出巡逻模式，请检查送货状态", logs

    nav_to_entry()
    with db_session() as conn:
        pending = count_pending_delivery(conn) > 0
    if pending:
        robot_sm.force_set(RobotState.PENDING_DELIVERY)
        logs.append("回 100 后 → pending_delivery")
    else:
        robot_sm.force_set(RobotState.IDLE)
        logs.append("回 100 后 → idle")
    return "已退出巡逻模式，回起点", logs


def robot_at_home(hb: Optional[dict[str, Any]] = None) -> bool:
    """根据心跳判断是否在起点附近；无 MQTT 时由 mock 驱动。"""
    from patrol_mode.config import HOME_ARRIVE_TOLERANCE_M, mock_vehicle_enabled

    if mock_vehicle_enabled():
        return False
    if not _mqtt_on():
        return False
    hb = hb or _bridge().snapshot()
    nav = str(hb.get("nav_state", "")).strip().upper()
    if nav and nav != "IDLE":
        return False
    goal = str(hb.get("current_goal_room", "") or "").strip()
    if goal == ENTRY_ROOM_ID:
        return True
    pose_x = hb.get("pose_x")
    pose_y = hb.get("pose_y")
    if pose_x is None or pose_y is None:
        return bool(hb.get("delivery_waiting"))
    from vehicle_rooms import ROOM_LOCATIONS

    anchor = ROOM_LOCATIONS.get(ENTRY_ROOM_ID, {})
    ax = float(anchor.get("x", 0))
    ay = float(anchor.get("y", 0))
    dx = float(pose_x) - ax
    dy = float(pose_y) - ay
    return (dx * dx + dy * dy) ** 0.5 <= HOME_ARRIVE_TOLERANCE_M


def load_task_config() -> PatrolTaskConfig:
    from patrol_mode.config import _TASK_CONFIG_FILE, ensure_data_dir
    import json

    ensure_data_dir()
    if not _TASK_CONFIG_FILE.is_file():
        cfg = PatrolTaskConfig()
        save_task_config(cfg)
        return cfg
    try:
        data = json.loads(_TASK_CONFIG_FILE.read_text(encoding="utf-8"))
        return PatrolTaskConfig.from_dict(data if isinstance(data, dict) else {})
    except Exception:
        return PatrolTaskConfig()


def save_task_config(cfg: PatrolTaskConfig) -> None:
    from patrol_mode.config import _TASK_CONFIG_FILE, ensure_data_dir
    import json

    ensure_data_dir()
    _TASK_CONFIG_FILE.write_text(
        json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
