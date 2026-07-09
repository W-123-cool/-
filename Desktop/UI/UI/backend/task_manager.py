"""
任务管理：待投件索引、投递队列语义（当前为单机器人 FIFO）、通知与异常占位。

扩展点：
- `PlaceholderRequest` 类型请求可在本模块增加分支而不破坏现有取货流。
- 超时扫描可在此注册定时器或 Celery beat（当前仅预留函数名与注释）。
"""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from database import db_session, fetch_all, fetch_one
from state_machine import RobotStateMachine, RobotState
from user_module import verify_login_password

# 单例状态机（MQTT_BRIDGE_ENABLED=0 时为唯一真源；启用桥时与车端 MQTT 协同）
robot_sm = RobotStateMachine()


def _mqtt_on() -> bool:
    try:
        from mqtt_robot_bridge import bridge_enabled

        return bridge_enabled()
    except Exception:
        return False


def _rollback_pickup_task(task_id: str) -> None:
    """pickup_request 被车端拒绝后：删任务与相关通知，并在无其它待投件时回到初态。"""
    with db_session() as conn:
        conn.execute("DELETE FROM notifications WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        pending = count_pending_delivery(conn) > 0
    if not pending:
        robot_sm.force_set(RobotState.IDLE)


def apply_mqtt_task_status(data: dict[str, Any]) -> None:
    """由 MQTT 桥线程调用：车端 task_status 与 SQLite 对齐。"""
    if not _mqtt_on():
        return
    if str(data.get("msg_type", "")).strip() != "task_status":
        return
    rid = str(data.get("request_id", "")).strip()
    st = str(data.get("status", "")).strip()
    if not rid:
        return
    if st == "waiting_receipt":
        with db_session() as conn:
            row = fetch_one(conn, "SELECT * FROM tasks WHERE id = ?", (rid,))
            if not row or str(row["status"]) != "delivering":
                return
            uid = int(row["user_id"])
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                ("await_pickup", _now(), rid),
            )
            _notify(conn, uid, "货物已送达", "请前往指定门牌，输入登录密码取件。", rid)
        robot_sm.on_robot_arrived_at_dropoff()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _notify(
    conn: Any, user_id: int, title: str, body: str, task_id: Optional[str] = None
) -> None:
    conn.execute(
        """INSERT INTO notifications (user_id, task_id, title, body)
           VALUES (?,?,?,?)""",
        (user_id, task_id, title, body),
    )


def normalize_match_key(key: str) -> str:
    """投件码：6 位数字字符串。"""
    return key.strip()


def _active_dropoff_codes(conn: Any) -> set[str]:
    rows = fetch_all(
        conn,
        """SELECT match_key FROM tasks
           WHERE status IN ('pending_delivery', 'delivering', 'await_pickup')""",
    )
    return {str(r["match_key"]).strip() for r in rows if str(r.get("match_key", "")).strip()}


def generate_unique_dropoff_code(conn: Any) -> str:
    """随机 6 位投件码，与当前活动任务队列不重复。"""
    used = _active_dropoff_codes(conn)
    for _ in range(200):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in used:
            return code
    raise RuntimeError("无法生成唯一投件码，请稍后重试")


def count_pending_delivery(conn: Any) -> int:
    row = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM tasks WHERE status = ?",
        ("pending_delivery",),
    )
    return int(row["c"]) if row else 0


def create_pickup_request(
    user_id: int, door_plate: str
) -> tuple[bool, str, Optional[str], Optional[str]]:
    """
    用户发起取货请求 -> 任务「待投件」，并自动生成 6 位投件码。
    返回 (成功, 消息, task_id, dropoff_code)。
    """
    door_plate = door_plate.strip()
    if not door_plate:
        return False, "门牌号不能为空", None, None

    if not robot_sm.can_accept_pickup_request():
        return False, "当前不可接单（机器人送货/待取货中）", None, None

    from master_mode import security_blocks_business

    blocked, reason = security_blocks_business()
    if blocked:
        return False, reason, None, None

    if _mqtt_on():
        from mqtt_robot_bridge import DELIVERY_ROOM_IDS

        if door_plate not in DELIVERY_ROOM_IDS:
            return (
                False,
                "MQTT 联调时 door_plate 须为车上导航房间号（与 switcher_node 一致），"
                f"例如: {', '.join(DELIVERY_ROOM_IDS)}",
                None,
                None,
            )

    tid = str(uuid.uuid4())
    with db_session() as conn:
        mk = generate_unique_dropoff_code(conn)
        conn.execute(
            """INSERT INTO tasks (id, user_id, door_plate, match_key, status, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (tid, user_id, door_plate, mk, "pending_delivery", _now()),
        )
        _notify(
            conn,
            user_id,
            "取货请求已提交",
            f"任务 {tid[:8]}… 门牌 {door_plate}，投件码 {mk}，请等待投件。",
            tid,
        )

    # 初态 -> 待投件：仅当机器人当前在 IDLE
    robot_sm.on_task_pending_created()

    if _mqtt_on():
        from mqtt_robot_bridge import get_bridge

        br = get_bridge()
        ack = br.publish_pickup_request(tid, mk, door_plate)
        if not bool(ack.get("ok")):
            _rollback_pickup_task(tid)
            return False, str(ack.get("reason", "车端拒绝 pickup_request")), None, None

    return True, "任务已创建", tid, mk


def _courier_try_dispatch_bridge(mk: str) -> tuple[bool, str, Optional[dict[str, Any]]]:
    from mqtt_robot_bridge import get_bridge
    from tour_manager import tour_blocks_courier

    blocked, reason = tour_blocks_courier()
    if blocked:
        return False, reason, None

    if robot_sm.state == RobotState.RETURNING:
        return False, "机器人返回中，不可投件", None

    br = get_bridge()
    if not br.delivery_ready_for_courier():
        return (
            False,
            "车端未处于可投件状态（需 MQTT 心跳：delivery_waiting 且 nav_state=IDLE）。",
            None,
        )

    with db_session() as conn:
        row = fetch_one(
            conn,
            """SELECT * FROM tasks WHERE status = ? AND match_key = ?
               ORDER BY datetime(created_at) ASC LIMIT 1""",
            ("pending_delivery", mk),
        )
        if not row:
            return False, "无匹配的待投件任务（投件码不存在）", None
        tid = str(row["id"])

    chk = br.publish_courier_dropoff(mk, tid)
    if not chk.get("ok"):
        return False, str(chk.get("reason", "投件码校验失败")), None
    if chk.get("need_select"):
        return False, "同投件码多个任务，请使用单车队列或清空后重试", {"candidates": chk.get("candidates")}

    start = br.publish_confirm_delivery(mk, tid)
    if not start.get("ok"):
        return False, str(start.get("reason", "无法开始送货")), None

    with db_session() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            ("delivering", _now(), tid),
        )

    if not robot_sm.on_courier_confirm_dispatch():
        robot_sm.force_set(RobotState.DELIVERING)

    return True, "已确认投件，进入送货中", {"task_id": tid, "robot": robot_sm.state.value}


def courier_try_dispatch(match_key: str) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """
    送货员投件：按投件码查找首条待投件任务；校验机器人是否允许投件。
    失败：无任务 / 当前状态不可投件（误操作不产生副作用）。
    """
    mk = normalize_match_key(match_key)
    if not mk:
        return False, "请输入 6 位投件码", None
    if not (mk.isdigit() and len(mk) == 6):
        return False, "投件码须为 6 位数字", None

    if robot_sm.state == RobotState.RETURNING:
        return False, "机器人返回中，不可投件（请等待回初始点）", None

    from tour_manager import tour_blocks_courier

    blocked, reason = tour_blocks_courier()
    if blocked:
        return False, reason, None

    from master_mode import security_blocks_business

    sec, sec_reason = security_blocks_business()
    if sec:
        return False, sec_reason, None

    if _mqtt_on():
        return _courier_try_dispatch_bridge(mk)

    if not robot_sm.can_courier_dispatch():
        return False, "当前机器人状态不可投件（可能已在送货中或未处于待投件）", None

    with db_session() as conn:
        row = fetch_one(
            conn,
            """SELECT * FROM tasks WHERE status = ? AND match_key = ?
               ORDER BY datetime(created_at) ASC LIMIT 1""",
            ("pending_delivery", mk),
        )
        if not row:
            return False, "无匹配的待投件任务（投件码不存在）", None
        tid = str(row["id"])
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            ("delivering", _now(), tid),
        )

    ok = robot_sm.on_courier_confirm_dispatch()
    if not ok:
        # 状态竞态：回滚任务状态
        with db_session() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                ("pending_delivery", _now(), tid),
            )
        return False, "投件确认时状态已变化，请重试", None

    return True, "已确认投件，进入送货中", {"task_id": tid, "robot": robot_sm.state.value}


def robot_mark_delivered(task_id: str) -> tuple[bool, str]:
    """车载上报：货物已送达站点 -> 待取货。"""
    if robot_sm.state == RobotState.RETURNING:
        return False, "机器人返回中，不可标记送达"
    with db_session() as conn:
        row = fetch_one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return False, "任务不存在"
        st = str(row["status"])
        if st == "await_pickup":
            return True, "任务已在待取货（可能已由 MQTT 自动同步）"
        if st != "delivering":
            return False, "任务不在送货中状态"

    if _mqtt_on():
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            with db_session() as conn:
                row = fetch_one(conn, "SELECT status FROM tasks WHERE id = ?", (task_id,))
            if row and str(row["status"]) == "await_pickup":
                return True, "车辆已到站，任务已进入待取货"
            time.sleep(0.35)
        return False, "等待车端到站超时：请确认导航成功且 MQTT 正常"

    with db_session() as conn:
        row = fetch_one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return False, "任务不存在"
        uid = int(row["user_id"])
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            ("await_pickup", _now(), task_id),
        )
        _notify(conn, uid, "货物已送达", "请前往指定门牌，输入登录密码取件。", task_id)

    if not robot_sm.on_robot_arrived_at_dropoff():
        return False, "状态机未处于送货中（模拟顺序有误？）"

    return True, "已标记送达，等待用户取货"


def user_verify_pickup(
    user_id: int, task_id: str, login_password: str
) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """
    用户在站点输入登录密码；任务标记完成；机器人进入「返回中」，直到送货端调用
    simulate_robot_return_home_complete（模拟回到出发点）。
    """
    with db_session() as conn:
        row = fetch_one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return False, "任务不存在", None
        if int(row["user_id"]) != user_id:
            return False, "无权操作此任务", None
        if str(row["status"]) != "await_pickup":
            return False, "当前不可取货（状态不匹配）", None

    if not verify_login_password(user_id, login_password):
        return False, "登录密码错误", None

    if _mqtt_on():
        from mqtt_robot_bridge import get_bridge

        r = get_bridge().publish_confirm_receipt(task_id)
        if not r.get("ok"):
            return False, str(r.get("reason", "车端拒绝 confirm_receipt")), None

    if not robot_sm.on_user_pickup_success():
        if not _mqtt_on():
            return False, "状态机未处于待取货", None
        robot_sm.force_set(RobotState.RETURNING)

    with db_session() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            ("completed", _now(), task_id),
        )
        _notify(conn, user_id, "取货成功", "感谢您的使用。", task_id)

    hint = (
        "取货成功（小车已返航，请在送货端「模拟回位」等待 MQTT 显示可再次投件）"
        if _mqtt_on()
        else "取货成功（小车已进入返回中，请在送货端「模拟回位」）"
    )
    meta = {"robot_state": robot_sm.state.value}
    return True, hint, meta


def list_user_tasks(user_id: int) -> list[dict[str, Any]]:
    with db_session() as conn:
        return fetch_all(
            conn,
            """SELECT id, door_plate, match_key, status, created_at, updated_at
               FROM tasks WHERE user_id = ? ORDER BY datetime(created_at) DESC""",
            (user_id,),
        )


def list_notifications(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = fetch_all(
            conn,
            """SELECT id, task_id, title, body, read_flag, created_at
               FROM notifications WHERE user_id = ?
               ORDER BY id DESC LIMIT ?""",
            (user_id, limit),
        )
    return rows


def mark_notification_read(user_id: int, notif_id: int) -> bool:
    with db_session() as conn:
        cur = conn.execute(
            "UPDATE notifications SET read_flag = 1 WHERE id = ? AND user_id = ?",
            (notif_id, user_id),
        )
        return cur.rowcount > 0


def list_tasks_for_courier() -> list[dict[str, Any]]:
    """
    送货员端列表：待投件 + 送货中（门牌/投件码/状态）。
    与既有任务表查询一致，不改变状态机与任务流转逻辑。
    """
    with db_session() as conn:
        return fetch_all(
            conn,
            """SELECT id, door_plate, match_key, status, created_at, updated_at
               FROM tasks
               WHERE status IN ('pending_delivery', 'delivering')
               ORDER BY datetime(created_at) ASC""",
            (),
        )


def query_can_courier_dispatch() -> tuple[bool, str]:
    """是否允许投件（导览互斥 + 机器人态）。"""
    from master_mode import security_blocks_business
    from tour_manager import tour_blocks_courier

    blocked, reason = security_blocks_business()
    if blocked:
        return False, reason
    blocked, reason = tour_blocks_courier()
    if blocked:
        return False, reason
    if robot_sm.state == RobotState.RETURNING:
        return False, "机器人返回中，不可投件"
    if not robot_sm.can_courier_dispatch():
        return False, f"当前为 {robot_sm.state.value}，仅待投件时可确认投件"
    return True, ""


def get_robot_snapshot() -> dict[str, Any]:
    from tour_manager import get_tour_status, query_can_start_tour

    tour = get_tour_status()
    ok_tour, tour_reason = query_can_start_tour()
    ok_courier, courier_reason = query_can_courier_dispatch()
    phase = str(tour.get("phase", "idle"))
    out: dict[str, Any] = {
        "robot_state": robot_sm.state.value,
        "tour": tour,
        "tour_busy": bool(tour.get("tour_busy")),
        "tour_phase": phase,
        "tour_phase_label_cn": tour.get("phase_label_cn", "初态"),
        "capabilities": {
            "can_start_tour": ok_tour,
            "can_start_tour_reason": tour_reason if not ok_tour else "",
            "can_courier_dispatch": ok_courier,
            "can_courier_dispatch_reason": courier_reason if not ok_courier else "",
            "can_voice_wake": phase in ("idle", "holding", "waiting_voice")
            or (phase == "navigating" and bool(tour.get("ui_locked"))),
            "can_tour_cancel": phase in ("navigating", "holding"),
        },
    }
    if _mqtt_on():
        try:
            from master_mode import (
                master_mode_from_snapshot,
                security_active_from_snapshot,
            )
            from mqtt_robot_bridge import get_bridge

            snap = get_bridge().snapshot()
            out["mqtt"] = snap
            out["master_mode"] = master_mode_from_snapshot(snap)
            out["security_active"] = security_active_from_snapshot(snap)
        except Exception as e:
            out["mqtt_error"] = str(e)
    try:
        from patrol_mode.service import get_patrol_service

        out["patrol"] = get_patrol_service().status_dict()
        if get_patrol_service().security_active():
            out["security_active"] = True
            out["master_mode"] = get_patrol_service().master_mode_label()
    except Exception:
        pass
    return out


def simulate_robot_return_home_complete() -> tuple[bool, str, dict[str, Any]]:
    """
    模拟小车从「返回中」回到出发点结束。
    若库里仍有 pending_delivery 任务 → pending_delivery；否则 → idle。
    """
    if robot_sm.state != RobotState.RETURNING:
        return (
            False,
            "当前机器人不在「返回中」，无法模拟回位（请先在取货端完成确认取货）。",
            {},
        )
    if _mqtt_on():
        from mqtt_robot_bridge import get_bridge

        ok = get_bridge().wait_idle_delivery_waiting(timeout=240.0)
        if not ok:
            return (
                False,
                "等待车端回位超时（MQTT 未出现 delivery_waiting + IDLE）。可检查导航与网络。",
                {},
            )
    with db_session() as conn:
        pending = count_pending_delivery(conn) > 0
    robot_sm.on_return_home_complete(still_has_pending=pending)
    final = robot_sm.state.value
    return True, "已模拟回位完成", {
        "robot_final": final,
        "had_pending_delivery": pending,
    }


def debug_clear_all_tasks_and_notifications_idle() -> dict[str, Any]:
    """调试：删除全部任务与通知，机器人强制初态。"""
    if _mqtt_on():
        try:
            from mqtt_robot_bridge import get_bridge

            get_bridge().publish_clear_tasks()
        except Exception:
            pass
    with db_session() as conn:
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM tasks")
    robot_sm.force_set(RobotState.IDLE)
    return {"robot_state": robot_sm.state.value, "message": "已清空全部任务与通知"}
