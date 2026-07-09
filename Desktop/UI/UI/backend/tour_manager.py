"""导览会话（内存，后端权威）：六态 + 2 分钟待机/待语音超时；与送货共用 MQTT 桥。"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from state_machine import RobotState
from task_manager import count_pending_delivery, robot_sm
from database import db_session
from tour_phases import (
    ACTIVE_PHASES,
    HOLDING_TIMEOUT_SEC,
    PHASE_AT_DEST,
    PHASE_HOLDING,
    PHASE_IDLE,
    PHASE_NAVIGATING,
    PHASE_RETURNING,
    PHASE_WAITING_VOICE,
    WAITING_VOICE_TIMEOUT_SEC,
    is_tour_busy,
    normalize_phase,
    phase_label_cn,
    vehicle_nav_moving,
)
from vehicle_rooms import TOUR_ROOM_IDS


def _mqtt_on() -> bool:
    from mqtt_robot_bridge import bridge_enabled

    return bridge_enabled()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timeout_sec(kind: str) -> float:
    if kind == "holding":
        try:
            return float(os.environ.get("TOUR_HOLDING_TIMEOUT_SEC", HOLDING_TIMEOUT_SEC))
        except ValueError:
            return HOLDING_TIMEOUT_SEC
    try:
        return float(os.environ.get("TOUR_WAITING_VOICE_TIMEOUT_SEC", WAITING_VOICE_TIMEOUT_SEC))
    except ValueError:
        return WAITING_VOICE_TIMEOUT_SEC


@dataclass
class TourSession:
    tour_id: str
    room: str
    phase: str
    created_at: str = field(default_factory=_now_iso)
    message: str = ""
    phase_deadline_mono: Optional[float] = None
    pending_voice_room: str = ""


_lock = threading.Lock()
_active: Optional[TourSession] = None


def _set_deadline(session: TourSession, kind: str) -> None:
    session.phase_deadline_mono = time.monotonic() + _timeout_sec(kind)


def _clear_deadline(session: TourSession) -> None:
    session.phase_deadline_mono = None


def _vehicle_nav_state() -> str:
    if not _mqtt_on():
        return ""
    try:
        from mqtt_robot_bridge import get_bridge

        return str(get_bridge().snapshot().get("nav_state", "")).strip()
    except Exception:
        return ""


def _status_extras(phase: str) -> dict[str, Any]:
    nav = _vehicle_nav_state()
    moving = vehicle_nav_moving(nav)
    ui_locked = normalize_phase(phase) == PHASE_NAVIGATING and moving
    return {
        "phase_label_cn": phase_label_cn(phase),
        "tour_busy": is_tour_busy(phase),
        "vehicle_nav_state": nav,
        "vehicle_moving": moving,
        "ui_locked": ui_locked,
        "blocks_courier_dispatch": is_tour_busy(phase),
    }


def _session_public(s: TourSession) -> dict[str, Any]:
    out: dict[str, Any] = {
        "active": True,
        "tour_id": s.tour_id,
        "room": s.room,
        "phase": normalize_phase(s.phase),
        "message": s.message,
        "created_at": s.created_at,
        "pending_voice_room": s.pending_voice_room,
    }
    if s.phase_deadline_mono is not None:
        remain = max(0.0, s.phase_deadline_mono - time.monotonic())
        out["phase_deadline_sec_remaining"] = round(remain, 1)
    out.update(_status_extras(s.phase))
    return out


def get_tour_status() -> dict[str, Any]:
    poll_tour_arrival(timeout=0.0)
    poll_tour_timeouts()
    poll_tour_return_complete(timeout=0.0)
    with _lock:
        if not _active:
            out: dict[str, Any] = {"active": False, "phase": PHASE_IDLE}
            out.update(_status_extras(PHASE_IDLE))
        else:
            out = _session_public(_active)
    out["robot_state"] = robot_sm.state.value
    out["mqtt_bridge_enabled"] = _mqtt_on()
    return out


def tour_blocks_courier() -> tuple[bool, str]:
    """导览占用时禁止投件（车载 / courier_client / API 统一）。"""
    st = get_tour_status()
    if bool(st.get("tour_busy")):
        label = str(st.get("phase_label_cn") or st.get("phase") or "导览中")
        return True, f"导览进行中（{label}），不可投件"
    return False, ""


def query_can_start_tour() -> tuple[bool, str]:
    """是否允许新开导览（供 capabilities / 各客户端查询）。"""
    return _can_start_new_tour()


def _delivery_blocks_new_tour() -> Optional[str]:
    if robot_sm.state in (RobotState.DELIVERING, RobotState.AWAIT_PICKUP):
        return f"送货进行中（{robot_sm.state.value}），不可开导览"
    # UI 不可开新导览；语音强占在 P3
    if robot_sm.state == RobotState.RETURNING:
        return "机器人送货返航中，请用语音截停接入导览"
    return None


def _vehicle_busy_reason() -> Optional[str]:
    blocked = _delivery_blocks_new_tour()
    if blocked and robot_sm.state != RobotState.RETURNING:
        return blocked
    if not _mqtt_on():
        return None
    from mqtt_robot_bridge import get_bridge

    br = get_bridge()
    snap = br.snapshot()
    if not snap.get("connected"):
        return "MQTT 未连接车端，请检查 broker 与 switcher_node"
    with _lock:
        cur = _active
    if cur and normalize_phase(cur.phase) == PHASE_NAVIGATING:
        return None
    nav = str(snap.get("nav_state", "")).strip()
    if nav and nav not in ("IDLE",):
        return f"车端导航忙（nav_state={nav}），请稍后再开导览"
    return None


def _can_start_new_tour() -> tuple[bool, str]:
    with _lock:
        if _active and normalize_phase(_active.phase) in ACTIVE_PHASES:
            return False, "导览会话进行中，请先结束或取消"
    blocked = _delivery_blocks_new_tour()
    if blocked and robot_sm.state != RobotState.RETURNING:
        return False, blocked
    busy = _vehicle_busy_reason()
    if busy:
        return False, busy
    from master_mode import security_blocks_business

    sec, reason = security_blocks_business()
    if sec:
        return False, reason
    return True, ""


def pause_tour_deadlines() -> None:
    with _lock:
        if _active:
            _active.phase_deadline_mono = None


def resume_tour_deadlines() -> None:
    with _lock:
        if not _active:
            return
        ph = normalize_phase(_active.phase)
        if ph == PHASE_WAITING_VOICE:
            _set_deadline(_active, "waiting_voice")
        elif ph == PHASE_HOLDING:
            _set_deadline(_active, "holding")


def seize_from_delivery_return() -> tuple[bool, str, Optional[dict[str, Any]]]:
    """送货返航中语音强占 → 导览原地待机（送货队列与 returning 态保留）。"""
    global _active
    if robot_sm.state != RobotState.RETURNING:
        return False, "当前不在送货返航中", None
    with _lock:
        if _active and normalize_phase(_active.phase) in ACTIVE_PHASES:
            return False, "已有导览会话", None

    if _mqtt_on():
        from mqtt_robot_bridge import get_bridge

        get_bridge().publish_nav_cancel("voice_seize_delivery_return")

    tid = str(uuid.uuid4())
    session = TourSession(
        tour_id=tid,
        room="",
        phase=PHASE_HOLDING,
        message="已强占送货返航，导览原地待机",
    )
    _set_deadline(session, "holding")
    with _lock:
        _active = session
    return True, "已进入导览原地待机", {"tour_id": tid, "phase": PHASE_HOLDING}


def start_tour_from_voice(room: str) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """待语音/原地待机：语音确认导航。"""
    global _active
    room = room.strip()
    if not room or room not in TOUR_ROOM_IDS:
        return False, f"房间须为: {', '.join(TOUR_ROOM_IDS)}", None
    with _lock:
        cur = _active
    if not cur:
        return start_tour(room, discard_voice=True)
    ph = normalize_phase(cur.phase)
    if ph == PHASE_NAVIGATING:
        return False, "已在导览中", None
    if ph not in (PHASE_WAITING_VOICE, PHASE_HOLDING, PHASE_AT_DEST):
        return start_tour(room, discard_voice=True)
    tid = cur.tour_id
    ok_nav, nav_msg = _dispatch_nav(tid, room)
    if not ok_nav:
        return False, nav_msg, None
    with _lock:
        if _active and _active.tour_id == tid:
            _active.room = room
            _active.phase = PHASE_NAVIGATING
            _active.message = "语音导览已发车"
            _active.pending_voice_room = ""
            _clear_deadline(_active)
    return True, "导览导航已下发", {"tour_id": tid, "room": room, "phase": PHASE_NAVIGATING}


def voice_utterance(
    *,
    intent: str,
    room: str = "",
    text: str = "",
) -> tuple[bool, str]:
    """P3：语音意图上报（车端 STT/意图解析后调用）。"""
    intent = str(intent or "unknown").strip().lower()
    room = str(room or "").strip()
    if text.strip():
        voice_touch()

    if intent in ("end_session", "bye"):
        voice_discard()
        return True, "会话结束"

    if intent == "cancel":
        with _lock:
            ph = normalize_phase(_active.phase) if _active else PHASE_IDLE
        if ph == PHASE_NAVIGATING:
            ok, msg = enter_holding_from_nav_stop(message="语音截停，原地待机")
            return ok, msg
        if ph in (PHASE_HOLDING, PHASE_AT_DEST):
            ok, msg = holding_cancel_confirm()
            return ok, msg
        return True, "无导览可取消"

    if intent == "navigate" and room:
        ok, msg, _data = start_tour_from_voice(room)
        return ok, msg

    if intent == "navigate" and not room:
        return False, "未识别目标房间"

    if intent == "qa":
        if room:
            voice_set_pending_room(room)
        return True, "咨询已记录"

    return True, "已处理"


def voice_wake() -> tuple[bool, str, Optional[dict[str, Any]]]:
    """进入待按键语音输入（P3 语音 / P2 UI 占位均可调用）。"""
    global _active
    with _lock:
        if _active:
            ph = normalize_phase(_active.phase)
            if ph == PHASE_HOLDING:
                _set_deadline(_active, "holding")
                return True, "已在原地待机，已刷新等待", {
                    "tour_id": _active.tour_id,
                    "phase": PHASE_HOLDING,
                }
            if ph == PHASE_AT_DEST:
                _set_deadline(_active, "holding")
                return True, "已抵达目的地，可继续语音", {
                    "tour_id": _active.tour_id,
                    "phase": PHASE_AT_DEST,
                }
            if ph == PHASE_WAITING_VOICE:
                _set_deadline(_active, "waiting_voice")
                return True, "已在待按键语音输入", {
                    "tour_id": _active.tour_id,
                    "phase": PHASE_WAITING_VOICE,
                }
            if ph == PHASE_NAVIGATING:
                return True, "导览进行中，可说唤醒词截停", {
                    "tour_id": _active.tour_id,
                    "phase": PHASE_NAVIGATING,
                }
            if ph == PHASE_RETURNING:
                return True, "正在返回起点", {
                    "tour_id": _active.tour_id,
                    "phase": PHASE_RETURNING,
                }
    if robot_sm.state == RobotState.RETURNING:
        return seize_from_delivery_return()
    ok, msg = _can_start_new_tour()
    if not ok:
        return False, msg, None
    tid = str(uuid.uuid4())
    session = TourSession(
        tour_id=tid,
        room="",
        phase=PHASE_WAITING_VOICE,
        message="已唤醒，请按键开始语音输入",
    )
    _set_deadline(session, "waiting_voice")
    with _lock:
        _active = session
    return True, "已进入待按键语音输入", {"tour_id": tid, "phase": PHASE_WAITING_VOICE}


def voice_discard() -> tuple[bool, str]:
    """丢弃待语音会话（如 UI 确认导览抢占）。"""
    global _active
    with _lock:
        if not _active:
            return True, "无待丢弃会话"
        if normalize_phase(_active.phase) != PHASE_WAITING_VOICE:
            return False, f"当前为 {_active.phase}，非待语音态"
        _active = None
    return True, "已取消待语音输入"


def voice_touch() -> tuple[bool, str]:
    """待语音/原地待机：用户有操作，重置超时（P3 录音前/后调用）。"""
    with _lock:
        if not _active:
            return False, "无导览会话"
        ph = normalize_phase(_active.phase)
        if ph == PHASE_WAITING_VOICE:
            _set_deadline(_active, "waiting_voice")
            return True, "已重置待语音超时"
        if ph == PHASE_HOLDING or ph == PHASE_AT_DEST:
            _set_deadline(_active, "holding")
            return True, "已重置原地待机超时"
    return False, "当前阶段不支持刷新超时"


def voice_set_pending_room(room: str) -> tuple[bool, str]:
    """待语音态：记录已解析、尚未发车的房间（超时仍可发车）。"""
    room = room.strip()
    if room not in TOUR_ROOM_IDS:
        return False, f"房间须为: {', '.join(TOUR_ROOM_IDS)}"
    with _lock:
        if not _active or normalize_phase(_active.phase) != PHASE_WAITING_VOICE:
            return False, "当前不在待按键语音输入态"
        _active.pending_voice_room = room
        _active.message = f"已识别目标 {room}，请继续语音或等待发车"
    return True, f"已记录待导览房间 {room}"


def _dispatch_nav(tid: str, room: str) -> tuple[bool, str]:
    """内部：下发导览导航。"""
    if _mqtt_on():
        from mqtt_robot_bridge import get_bridge

        br = get_bridge()
        ack = br.publish_tour_nav(tid, room)
        if not ack.get("ok"):
            return False, str(ack.get("reason", "车端拒绝导览导航"))
    return True, "导览导航已下发"


def start_tour(room: str, *, discard_voice: bool = True) -> tuple[bool, str, Optional[dict[str, Any]]]:
    global _active
    room = room.strip()
    if not room:
        return False, "请选择目标房间", None
    if room not in TOUR_ROOM_IDS:
        return False, f"房间须为以下之一: {', '.join(TOUR_ROOM_IDS)}", None

    with _lock:
        cur = _active
        if cur and normalize_phase(cur.phase) == PHASE_WAITING_VOICE:
            if discard_voice:
                _active = None
            else:
                return False, "请先结束待语音输入", None

    ok, msg = _can_start_new_tour()
    if not ok:
        return False, msg, None

    tid = str(uuid.uuid4())
    session = TourSession(tour_id=tid, room=room, phase=PHASE_NAVIGATING, message="正在发车")

    ok_nav, nav_msg = _dispatch_nav(tid, room)
    if not ok_nav:
        return False, nav_msg, None

    with _lock:
        _active = session
        _clear_deadline(_active)

    return True, "导览导航已下发", {"tour_id": tid, "room": room, "phase": PHASE_NAVIGATING}


def apply_tour_arrived(data: dict[str, Any]) -> None:
    global _active
    if str(data.get("msg_type", "")).strip() != "tour_arrived":
        return
    tid = str(data.get("tour_id", "")).strip()
    room = str(data.get("room", "")).strip()
    with _lock:
        if not _active or _active.tour_id != tid:
            return
        if normalize_phase(_active.phase) != PHASE_NAVIGATING:
            return
        _active.phase = PHASE_AT_DEST
        _active.message = f"已到达 {room or _active.room}，可继续语音交互"
        _set_deadline(_active, "holding")


def poll_tour_arrival(timeout: float = 0.0) -> bool:
    global _active
    with _lock:
        cur = _active
    if not cur or normalize_phase(cur.phase) != PHASE_NAVIGATING:
        return cur is not None and normalize_phase(cur.phase) in (PHASE_HOLDING, PHASE_AT_DEST)

    if not _mqtt_on():
        return False

    from mqtt_robot_bridge import get_bridge

    br = get_bridge()
    if timeout > 0:
        msg = br.wait_tour_arrived(cur.tour_id, timeout=timeout)
        if msg:
            apply_tour_arrived(msg)
            return True

    arrived = br.peek_tour_arrived(cur.tour_id)
    if arrived:
        apply_tour_arrived(arrived)
        return True

    if br.heartbeat_tour_arrived(cur.room):
        with _lock:
            if (
                _active
                and _active.tour_id == cur.tour_id
                and normalize_phase(_active.phase) == PHASE_NAVIGATING
            ):
                _active.phase = PHASE_AT_DEST
                _active.message = f"已到达 {cur.room}（心跳），可继续语音交互"
                _set_deadline(_active, "holding")
        return True
    return False


def poll_tour_timeouts() -> bool:
    """待语音 / 原地待机 超时处理。"""
    global _active
    with _lock:
        cur = _active
    if not cur or cur.phase_deadline_mono is None:
        return False
    if time.monotonic() < cur.phase_deadline_mono:
        return False

    ph = normalize_phase(cur.phase)
    if ph == PHASE_WAITING_VOICE:
        pending = cur.pending_voice_room.strip()
        tid = cur.tour_id
        if pending and pending in TOUR_ROOM_IDS:
            ok_nav, nav_msg = _dispatch_nav(tid, pending)
            with _lock:
                if not _active or _active.tour_id != tid:
                    return True
                if ok_nav:
                    _active.room = pending
                    _active.phase = PHASE_NAVIGATING
                    _active.message = "超时自动发车"
                    _active.pending_voice_room = ""
                    _clear_deadline(_active)
                else:
                    _active.message = f"超时发车失败: {nav_msg}"
                    _active = None
            return True
        with _lock:
            _active = None
        return True

    if ph in (PHASE_HOLDING, PHASE_AT_DEST):
        ok, _msg = begin_return_home(reason="holding_timeout")
        return ok

    return False


def begin_return_home(*, reason: str = "user_cancel") -> tuple[bool, str]:
    """原地待机取消 / 超时 → 返回起点。"""
    global _active
    with _lock:
        if not _active:
            return False, "当前无导览会话"
        ph = normalize_phase(_active.phase)
        if ph == PHASE_RETURNING:
            return True, "已在返回起点"
        if ph not in (PHASE_HOLDING, PHASE_NAVIGATING, PHASE_AT_DEST):
            return False, f"当前阶段 {ph} 不可返航"
        tid = _active.tour_id
        room = _active.room

    if not robot_sm.on_tour_end_begin_return():
        return False, "机器人状态不允许进入返回中（送货/待取货中？）"

    if _mqtt_on():
        from mqtt_robot_bridge import get_bridge

        br = get_bridge()
        if ph == PHASE_NAVIGATING:
            br.publish_tour_stop_in_place(tid)
        br.publish_tour_return_home(tid)
        ack = br.wait_tour_return_result(tid, timeout=12.0)
        if not ack.get("ok"):
            return False, str(ack.get("reason", "车端未接受返航指令"))

    with _lock:
        if _active and _active.tour_id == tid:
            _active.phase = PHASE_RETURNING
            _active.message = f"返回起点（{reason}，自 {room}）"
            _clear_deadline(_active)

    return True, "已进入返回起点，期间仅可接受新取货请求"


def holding_cancel_confirm() -> tuple[bool, str]:
    return begin_return_home(reason="holding_cancel")


def enter_holding_from_nav_stop(*, message: str = "已截停，原地待机") -> tuple[bool, str]:
    """导览途中截停 → 原地待机（P3 唤醒截停 / 本地联调）。"""
    global _active
    with _lock:
        if not _active:
            return False, "当前无导览会话"
        ph = normalize_phase(_active.phase)
        if ph not in (PHASE_NAVIGATING, PHASE_RETURNING):
            return False, f"当前 {ph} 不可进入原地待机"
        tid = _active.tour_id

    if _mqtt_on():
        from mqtt_robot_bridge import get_bridge

        get_bridge().publish_tour_stop_in_place(tid)

    with _lock:
        if _active and _active.tour_id == tid:
            _active.phase = PHASE_HOLDING
            _active.message = message
            _set_deadline(_active, "holding")
    return True, message


def _finalize_return_home() -> None:
    global _active
    with db_session() as conn:
        pending = count_pending_delivery(conn) > 0
    robot_sm.on_return_home_complete(still_has_pending=pending)
    with _lock:
        _active = None


def poll_tour_return_complete(timeout: float = 0.0) -> bool:
    global _active
    with _lock:
        cur = _active
    if not cur or normalize_phase(cur.phase) != PHASE_RETURNING:
        return False

    if not _mqtt_on():
        return False

    from mqtt_robot_bridge import get_bridge

    br = get_bridge()
    tid = cur.tour_id

    if timeout > 0:
        msg = br.wait_tour_return_complete(tid, timeout=timeout)
        if msg and msg.get("ok"):
            _finalize_return_home()
            return True
        if br.wait_idle_delivery_waiting(timeout=timeout):
            _finalize_return_home()
            return True
        return False

    msg = br.peek_tour_return_complete(tid)
    if msg and msg.get("ok"):
        _finalize_return_home()
        return True
    if robot_sm.state == RobotState.RETURNING and br.heartbeat_at_entry_idle():
        _finalize_return_home()
        return True
    return False


# 兼容旧 API 名称
def finish_tour() -> tuple[bool, str]:
    return holding_cancel_confirm()


def cancel_tour() -> tuple[bool, str]:
    return holding_cancel_confirm()


def simulate_arrived() -> tuple[bool, str]:
    """无 MQTT 联调：手动到站 → 原地待机。"""
    global _active
    with _lock:
        if not _active or normalize_phase(_active.phase) != PHASE_NAVIGATING:
            return False, "仅导览中可模拟到站"
        room = _active.room
        _active.phase = PHASE_HOLDING
        _active.message = f"已到达 {room}（模拟），原地待机"
        _set_deadline(_active, "holding")
    return True, "已进入原地待机"
