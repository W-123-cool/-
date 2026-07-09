"""
车载集成真源：导览状态机（本地）+ 送货状态（本地模拟或 HTTP/MQTT 后端）。

模式 ONBOARD_MODE（默认 api）：
- api：送货/队列走 FastAPI；取货请求、确认取货由 **user_client** 发起，车载端不模拟。
- local：仅练导览状态机（障碍仍为模拟）；送货 Tab 提示改用 api 联调。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable, Optional

from .tour_state_machine import NaviState, NaviStateMachine

# 与 backend/state_machine 对齐
class RobotState(str, Enum):
    IDLE = "idle"
    PENDING_DELIVERY = "pending_delivery"
    DELIVERING = "delivering"
    AWAIT_PICKUP = "await_pickup"
    RETURNING = "returning"


ROBOT_STATE_CN = {
    RobotState.IDLE: "初态",
    RobotState.PENDING_DELIVERY: "待投件",
    RobotState.DELIVERING: "送货中",
    RobotState.AWAIT_PICKUP: "待取货",
    RobotState.RETURNING: "返回中",
}

try:
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root / "backend"))
    from vehicle_rooms import TOUR_ROOM_IDS as DELIVERY_ROOM_IDS  # noqa: E402
except Exception:
    DELIVERY_ROOM_IDS = ("101", "102", "103", "104", "201", "202", "203", "204")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _LocalRobotSM:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state = RobotState.IDLE

    @property
    def state(self) -> RobotState:
        with self._lock:
            return self._state

    def force_set(self, new: RobotState) -> None:
        with self._lock:
            self._state = new

    def on_task_pending_created(self) -> None:
        with self._lock:
            if self._state == RobotState.IDLE:
                self._state = RobotState.PENDING_DELIVERY

    def on_courier_confirm_dispatch(self) -> bool:
        with self._lock:
            if self._state != RobotState.PENDING_DELIVERY:
                return False
            self._state = RobotState.DELIVERING
            return True

    def on_robot_arrived_at_dropoff(self) -> bool:
        with self._lock:
            if self._state != RobotState.DELIVERING:
                return False
            self._state = RobotState.AWAIT_PICKUP
            return True

    def on_user_pickup_success(self) -> bool:
        with self._lock:
            if self._state != RobotState.AWAIT_PICKUP:
                return False
            self._state = RobotState.RETURNING
            return True

    def on_return_home_complete(self, still_has_pending: bool) -> None:
        with self._lock:
            self._state = (
                RobotState.PENDING_DELIVERY if still_has_pending else RobotState.IDLE
            )

    def on_tour_end_begin_return(self) -> bool:
        with self._lock:
            if self._state in (RobotState.DELIVERING, RobotState.AWAIT_PICKUP):
                return False
            self._state = RobotState.RETURNING
            return True

    def can_courier_dispatch(self) -> bool:
        return self.state == RobotState.PENDING_DELIVERY


@dataclass
class LocalTask:
    id: str
    door_plate: str
    match_key: str
    status: str
    created_at: str = field(default_factory=_now)


class OnboardController:
    """导览 + 送货互斥与集成的单一控制器。"""

    def __init__(self) -> None:
        mode = os.environ.get("ONBOARD_MODE", "api").strip().lower()
        self.mode = "api" if mode == "api" else "local"
        self.pending_delivery_count: int = 0
        self.api_base = (
            os.environ.get("COURIER_API_BASE")
            or os.environ.get("PICKUP_API_BASE")
            or "http://127.0.0.1:8000"
        ).rstrip("/")

        self.tour = NaviStateMachine()
        self._local_robot = _LocalRobotSM()
        self._local_tasks: list[LocalTask] = []
        self._lock = Lock()
        self._integration_log: list[str] = []
        self._listeners: list[Callable[[], None]] = []
        self._building_catalog: Optional[dict[str, Any]] = None
        self._tour_mqtt_cached: Optional[bool] = None
        self._tour_api_cached: dict[str, Any] = {}

        self.tour.on("on_state_change", lambda *_: self._on_tour_changed())
        self.tour.on("on_log", lambda _e: self._on_tour_changed())

        self._log(f"集成控制器启动，模式={self.mode}")

    def add_listener(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in self._listeners:
            try:
                fn()
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        with self._lock:
            self._integration_log.append(entry)
            if len(self._integration_log) > 200:
                self._integration_log = self._integration_log[-120:]
        self._notify()

    def integration_logs(self) -> list[str]:
        with self._lock:
            return list(self._integration_log)

    def tour_is_idle(self) -> bool:
        return self.tour.is_idle()

    def tour_ui_locked(self) -> bool:
        if self.mode == "api":
            return bool(self._tour_api_cached.get("ui_locked"))
        return self.tour.state == NaviState.NAVIGATING

    def _sync_tour_api_cache(self, st: dict[str, Any]) -> None:
        self._tour_api_cached = dict(st or {})
        phase = str(st.get("phase", "idle"))
        room = str(st.get("room", "") or "")
        msg = str(st.get("message", "") or "")
        ui_locked = bool(st.get("ui_locked"))
        deadline = st.get("phase_deadline_sec_remaining")
        dr = float(deadline) if deadline is not None else None
        self.tour.sync_from_authority(
            phase,
            room,
            msg,
            ui_locked=ui_locked,
            deadline_remaining=dr,
        )

    def _delivery_robot_state_local(self) -> RobotState:
        return self._local_robot.state

    def _delivery_robot_state_api(self) -> Optional[str]:
        try:
            from courier_client import api

            data = api.api_robot_state(self.api_base)
            return str(data.get("robot_state", "") or "")
        except Exception:
            return None

    def delivery_robot_label(self) -> str:
        if self.mode == "local":
            st = self._delivery_robot_state_local()
            return f"{st.value}（{ROBOT_STATE_CN.get(st, '?')}）"
        rs = self._delivery_robot_state_api()
        if rs is None:
            return "—（未连接后端）"
        from courier_client.status_labels import fmt_robot_state

        return str(fmt_robot_state(str(rs)))

    def _security_blocks_from_robot_api(self) -> tuple[bool, str]:
        if self.mode == "local":
            return False, ""
        try:
            from courier_client import api

            data = api.api_robot_state(self.api_base)
            snap = data.get("mqtt") if isinstance(data.get("mqtt"), dict) else data
            mm = str(snap.get("master_mode", "idle")).strip().lower()
            if snap.get("security_active") is True or mm in (
                "patrol",
                "guard",
                "manual_takeover",
            ):
                return True, f"总控安防模式（{mm}），送货/导览暂不可用"
        except Exception:
            pass
        return False, ""

    def pending_count_local(self) -> int:
        return sum(1 for t in self._local_tasks if t.status == "pending_delivery")

    def mutual_banner(self) -> str:
        blocked, reason = self._security_blocks_from_robot_api()
        if blocked:
            return reason
        st = self.tour.state
        if st == NaviState.RETURNING:
            return "导览返回起点中：仅可取货请求；不可投件/新导览"
        if st == NaviState.HOLDING:
            return "导览原地待机：不可投件；可取货请求"
        if st == NaviState.NAVIGATING:
            if self.tour_ui_locked():
                return "导览行进中：可点「取消导览」停车返起点；不可投件"
            return "导览中：不可投件；可取货请求"
        if st == NaviState.WAITING_VOICE:
            return "待语音输入：不可投件；可取货请求"
        if self.mode == "local":
            rs = self._delivery_robot_state_local()
            if rs == RobotState.RETURNING:
                return "机器人送货返航中：UI不可开导览；语音截停可接入（P3）"
            if rs in (RobotState.DELIVERING, RobotState.AWAIT_PICKUP):
                return f"送货进行中（{ROBOT_STATE_CN.get(rs, '')}）：不可开导览"
            return ""
        rs = self._delivery_robot_state_api() or ""
        if rs == "returning":
            return "机器人送货返航中：UI不可开导览；语音截停可接入（P3）"
        if rs in ("delivering", "await_pickup"):
            return f"送货进行中：不可开导览（当前 {rs}）"
        return ""

    def can_start_tour(self) -> tuple[bool, str]:
        blocked, reason = self._security_blocks_from_robot_api()
        if blocked:
            return False, reason
        if self.tour.state == NaviState.RETURNING:
            return False, "导览返回起点中，请等待完成"
        if self.tour.state not in (NaviState.IDLE, NaviState.WAITING_VOICE):
            return False, "导览会话进行中，请先结束或取消"
        if self.mode == "local":
            st = self._delivery_robot_state_local()
            if st == RobotState.RETURNING:
                return False, "送货返航中，UI不可开导览"
            if st in (RobotState.DELIVERING, RobotState.AWAIT_PICKUP):
                return False, f"送货状态为 {ROBOT_STATE_CN.get(st)}，仅初态/待投件时可开导览"
            return True, ""
        rs = self._delivery_robot_state_api()
        if rs is None:
            return False, "无法连接后端，请启动 uvicorn 或切换为 local 模式"
        if rs == "returning":
            return False, "送货返航中，UI不可开导览"
        if rs in ("delivering", "await_pickup"):
            return False, f"送货状态为 {rs}，仅 idle/pending_delivery 时可开导览"
        return True, ""

    def can_courier_dispatch(self) -> tuple[bool, str]:
        blocked, reason = self._security_blocks_from_robot_api()
        if blocked:
            return False, reason
        if not self.tour_is_idle():
            return False, "导览进行中，不可投件"
        if self.mode == "local":
            if not self._local_robot.can_courier_dispatch():
                return False, "机器人不在待投件状态"
            return True, ""
        rs = self._delivery_robot_state_api()
        if rs is None:
            return False, "无法获取机器人状态"
        if rs == "returning":
            return False, "机器人返回中，不可投件"
        if rs != "pending_delivery":
            return False, f"当前为 {rs}，仅待投件时可确认投件"
        return True, ""

    def _on_tour_changed(self) -> None:
        self._log(f"导览 → {self.tour.state_label}" + (f" 目标={self.tour.target}" if self.tour.target else ""))
        self._notify()

    # --- 导览（带互斥）---

    def tour_uses_real_vehicle(self) -> bool:
        if self.mode != "api":
            return False
        if self._tour_mqtt_cached is not None:
            return self._tour_mqtt_cached
        try:
            from courier_client import api

            cat = api.api_building_rooms(self.api_base)
            self._building_catalog = cat
            self._tour_mqtt_cached = bool(cat.get("mqtt_bridge_enabled"))
        except Exception:
            self._tour_mqtt_cached = False
        return bool(self._tour_mqtt_cached)

    def api_fetch_building_rooms(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        if self.mode != "api":
            return None, "非 api 模式"
        try:
            from courier_client import api

            cat = api.api_building_rooms(self.api_base)
            self._building_catalog = cat
            self._tour_mqtt_cached = bool(cat.get("mqtt_bridge_enabled"))
            return cat, None
        except Exception as e:
            return None, str(e)

    def tour_selectable_room_ids(self) -> list[str]:
        if self._building_catalog:
            ids = self._building_catalog.get("tour_room_ids")
            if isinstance(ids, list) and ids:
                return [str(x) for x in ids]
        return list(DELIVERY_ROOM_IDS)

    def _tour_begin_local_sm(self, target: str) -> tuple[bool, str]:
        if self.tour.state == NaviState.WAITING_VOICE:
            self.tour.discard_waiting_voice()
        return self.tour.begin_navigating(target)

    def tour_begin_flow(self, target: str) -> tuple[bool, str]:
        ok, msg = self.can_start_tour()
        if not ok:
            return False, msg
        target = target.strip()
        if not target:
            return False, "请选择目标房间"
        allowed = self.tour_selectable_room_ids()
        if target not in allowed:
            return False, f"房间须为: {', '.join(allowed)}"

        if self.mode == "api":
            from courier_client import api

            try:
                if not self.tour_is_idle() and self.tour.state == NaviState.WAITING_VOICE:
                    api.api_tour_voice_discard(self.api_base)
                data = api.api_tour_start(self.api_base, target, discard_voice=True)
                st = api.api_tour_status(self.api_base)
                self._sync_tour_api_cache(st)
                self._log(f"导览发车 → {target}: {data.get('message', '')}")
                return True, str(data.get("message", "导览导航已下发"))
            except Exception as e:
                return False, str(e)

        ok, msg = self._tour_begin_local_sm(target)
        if ok:
            self._log(f"导览演练（无 MQTT）目标 {target}")
        return ok, msg

    def tour_voice_touch(self) -> tuple[bool, str]:
        if self.mode != "api":
            self.tour.touch_activity()
            return True, "已刷新本地超时"
        from courier_client import api

        try:
            api.api_tour_voice_touch(self.api_base)
            st = api.api_tour_status(self.api_base)
            self._sync_tour_api_cache(st)
            return True, "已刷新语音超时"
        except Exception as e:
            return False, str(e)

    def tour_voice_wake(self) -> tuple[bool, str]:
        if self.mode == "api":
            from courier_client import api

            try:
                data = api.api_tour_voice_wake(self.api_base)
                st = api.api_tour_status(self.api_base)
                self._sync_tour_api_cache(st)
                return True, str(data.get("message", "已进入待语音"))
            except Exception as e:
                return False, str(e)
        ok, msg = self.tour.enter_waiting_voice()
        if ok:
            self._log(msg)
        return ok, msg

    def voice_ui_tap(self) -> tuple[bool, str, str]:
        """全局语音按钮：wake -> begin -> end（任意 Tab 可用）。"""
        if self.mode != "api":
            return False, "UI 语音需 API 模式", "noop"
        from courier_client import api

        try:
            data = api.api_tour_voice_ptt_tap(self.api_base)
            st = api.api_tour_status(self.api_base)
            self._sync_tour_api_cache(st)
            action = str(data.get("action", "noop"))
            msg = str(data.get("message", ""))
            tour_msg = data.get("tour_message")
            if tour_msg:
                self._log(f"语音·导览: {tour_msg}")
            return bool(data.get("ok", True)), msg, action
        except Exception as e:
            return False, str(e), "noop"

    def voice_ptt_status(self) -> dict[str, object]:
        if self.mode != "api":
            return {}
        from courier_client import api

        try:
            return api.api_tour_voice_ptt_status(self.api_base)
        except Exception:
            return {}

    def tour_voice_ptt_begin(self) -> tuple[bool, str]:
        if self.mode != "api":
            return False, "UI 语音需 API 模式"
        from courier_client import api

        try:
            data = api.api_tour_voice_ptt_begin(self.api_base)
            return True, str(data.get("message", "开始录音"))
        except Exception as e:
            return False, str(e)

    def tour_voice_ptt_end(self) -> tuple[bool, str]:
        if self.mode != "api":
            return False, "UI 语音需 API 模式"
        from courier_client import api

        try:
            data = api.api_tour_voice_ptt_end(self.api_base)
            return True, str(data.get("message", "结束录音"))
        except Exception as e:
            return False, str(e)

    def tour_voice_ptt_status(self) -> dict[str, object]:
        if self.mode != "api":
            return {}
        from courier_client import api

        try:
            return api.api_tour_voice_ptt_status(self.api_base)
        except Exception:
            return {}

    def tour_poll_status(self) -> None:
        """轮询后端权威态（api 模式）或本地返航完成。"""
        if self.mode == "api":
            try:
                from courier_client import api

                st = api.api_tour_status(self.api_base)
                self._sync_tour_api_cache(st)
            except Exception:
                pass
            return

        if self.tour.state == NaviState.RETURNING:
            st = self._delivery_robot_state_local()
            if st in (RobotState.IDLE, RobotState.PENDING_DELIVERY):
                ok, msg = self.tour.on_return_home_complete()
                if ok:
                    self._log(f"本地返航完成: {msg}")
                    self._notify()

    def tour_holding_cancel_flow(self) -> tuple[bool, str]:
        if self.tour.state not in (NaviState.HOLDING, NaviState.NAVIGATING):
            return False, "仅导览中或原地待机可取消导览"

        if self.mode == "api":
            from courier_client import api

            try:
                data = api.api_tour_holding_cancel(self.api_base)
                st = api.api_tour_status(self.api_base)
                self._sync_tour_api_cache(st)
                self._log(f"导览取消返航: {data.get('message', '')}")
                return True, str(data.get("message", "已进入返回起点"))
            except Exception as e:
                return False, str(e)

        ok, msg = self.tour.holding_cancel_confirm()
        if not ok:
            return False, msg
        if self.mode == "local":
            if not self._local_robot.on_tour_end_begin_return():
                return False, "本地机器人状态不允许返回中"
        self._log(f"导览取消: {msg}")
        self._notify()
        return True, msg

    def tour_action(self, action: str) -> tuple[bool, str]:
        if action == "holding_cancel":
            return self.tour_holding_cancel_flow()
        if action == "simulate_arrived":
            if self.mode == "api" and self.tour_uses_real_vehicle():
                from courier_client import api

                try:
                    data = api.api_tour_simulate_arrived(self.api_base)
                    st = api.api_tour_status(self.api_base)
                    self._sync_tour_api_cache(st)
                    return True, str(data.get("message", "已到站"))
                except Exception as e:
                    return False, str(e)
            ok, msg = self.tour.on_arrived()
            if ok:
                self._log(msg)
            return ok, msg
        if action == "voice_wake":
            return self.tour_voice_wake()
        if action == "voice_touch":
            return self.tour_voice_touch()
        return False, "未知操作"

    def tour_simulate_arrive_allowed(self) -> bool:
        if self.tour.state != NaviState.NAVIGATING:
            return False
        if self.mode == "api":
            return not self.tour_uses_real_vehicle()
        return True

    # --- 送货：本地 ---

    def local_simulate_pickup(self, door_plate: str, match_key: str) -> tuple[bool, str]:
        door_plate = door_plate.strip()
        match_key = match_key.strip()
        if not door_plate or not match_key:
            return False, "门牌与匹配键不能为空"
        if door_plate not in DELIVERY_ROOM_IDS:
            return False, f"门牌须为 {', '.join(DELIVERY_ROOM_IDS)} 之一"
        tid = str(uuid.uuid4())
        with self._lock:
            self._local_tasks.append(
                LocalTask(id=tid, door_plate=door_plate, match_key=match_key, status="pending_delivery")
            )
        self._local_robot.on_task_pending_created()
        self._log(f"模拟取货请求 {tid[:8]}… 门牌 {door_plate}")
        self._notify()
        return True, f"已创建待投件任务 {tid[:8]}…"

    def local_list_queue(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": t.id,
                    "door_plate": t.door_plate,
                    "match_key": t.match_key,
                    "status": t.status,
                    "created_at": t.created_at,
                }
                for t in self._local_tasks
                if t.status in ("pending_delivery", "delivering")
            ]

    def local_courier_confirm(self, match_key: str) -> tuple[bool, str, Optional[dict]]:
        ok, msg = self.can_courier_dispatch()
        if not ok:
            return False, msg, None
        mk = match_key.strip()
        with self._lock:
            row = next(
                (t for t in self._local_tasks if t.status == "pending_delivery" and t.match_key == mk),
                None,
            )
            if not row:
                return False, "无匹配的待投件任务", None
            row.status = "delivering"
            tid = row.id
        if not self._local_robot.on_courier_confirm_dispatch():
            with self._lock:
                for t in self._local_tasks:
                    if t.id == tid:
                        t.status = "pending_delivery"
            return False, "投件时机器人状态已变化", None
        self._log(f"投件确认 {tid[:8]}…")
        self._notify()
        return True, "已确认投件", {"task_id": tid}

    def local_mark_delivered(self, task_id: str) -> tuple[bool, str]:
        if not self.tour_is_idle():
            return False, "导览进行中，不可标记送达"
        tid = task_id.strip()
        with self._lock:
            row = next((t for t in self._local_tasks if t.id == tid), None)
            if not row:
                return False, "任务不存在"
            if row.status == "await_pickup":
                return True, "已在待取货"
            if row.status != "delivering":
                return False, "任务不在送货中"
            row.status = "await_pickup"
        if not self._local_robot.on_robot_arrived_at_dropoff():
            return False, "机器人状态不是送货中"
        self._log(f"标记送达 {tid[:8]}… → 待取货")
        self._notify()
        return True, "已标记送达"

    def local_simulate_pickup_verify(self, task_id: str) -> tuple[bool, str]:
        tid = task_id.strip()
        with self._lock:
            row = next((t for t in self._local_tasks if t.id == tid), None)
            if not row or row.status != "await_pickup":
                return False, "任务不在待取货"
            row.status = "completed"
        if not self._local_robot.on_user_pickup_success():
            return False, "机器人状态不是待取货"
        self._log(f"模拟用户取货 {tid[:8]}… → 返回中")
        self._notify()
        return True, "已模拟取货，机器人进入返回中"

    def local_return_home(self) -> tuple[bool, str]:
        if self._local_robot.state != RobotState.RETURNING:
            return False, "当前不在返回中"
        pending = self.pending_count_local() > 0
        self._local_robot.on_return_home_complete(still_has_pending=pending)
        self._log("模拟回位完成")
        self._notify()
        return True, "已模拟回位"

    def local_clear_all(self) -> None:
        with self._lock:
            self._local_tasks.clear()
        self._local_robot.force_set(RobotState.IDLE)
        self._log("已清空本地任务")
        self._notify()

    # --- 送货：API ---

    def api_fetch_queue_and_robot(self) -> tuple[list[dict], dict, Optional[str]]:
        from courier_client import api

        try:
            q = api.api_queue(self.api_base)
            st = api.api_robot_state(self.api_base)
            self.pending_delivery_count = sum(
                1 for t in q if str(t.get("status", "")) == "pending_delivery"
            )
            return q, st, None
        except Exception as e:
            return [], {}, str(e)

    def delivery_requires_api(self) -> bool:
        return self.mode != "api"

    def api_courier_confirm(self, match_key: str) -> tuple[bool, str, Any]:
        ok, msg = self.can_courier_dispatch()
        if not ok:
            return False, msg, None
        from courier_client import api

        try:
            data = api.api_confirm(self.api_base, match_key)
            self._log(f"API 投件: {data.get('message', 'ok')}")
            self._notify()
            return True, str(data.get("message", "ok")), data
        except Exception as e:
            return False, str(e), None

    def api_mark_delivered(self, task_id: str) -> tuple[bool, str]:
        if not self.tour_is_idle():
            return False, "导览进行中，不可标记送达"
        from courier_client import api

        try:
            data = api.api_mark_delivered(self.api_base, task_id)
            self._log(f"API 标记送达: {data.get('message', 'ok')}")
            self._notify()
            return True, str(data.get("message", "ok"))
        except Exception as e:
            return False, str(e)

    def api_return_home(self) -> tuple[bool, str, Any]:
        from courier_client import api

        try:
            data = api.api_robot_return_home(self.api_base)
            self._log("API 模拟回位")
            self._notify()
            return True, str(data.get("message", "ok")), data
        except Exception as e:
            return False, str(e), None

    def api_clear_all(self) -> tuple[bool, str]:
        from courier_client import api

        try:
            data = api.api_debug_clear_all_tasks(self.api_base)
            self._log("API 清空任务")
            self._notify()
            return True, str(data.get("message", "ok"))
        except Exception as e:
            return False, str(e)


_controller: Optional[OnboardController] = None


def get_controller() -> OnboardController:
    global _controller
    if _controller is None:
        _controller = OnboardController()
    return _controller
