"""
MQTT 桥：与 ros_ws smart_nav_manager.switcher_node 对齐（robot/{id}/request|status）。

环境变量（B 方案：独立服务器 + 车端 ROS）：
- MQTT_BRIDGE_ENABLED：1 / true / yes 时启用；未设置则关闭（纯本地 SQLite 状态机）。
- MQTT_BROKER_HOST：默认 broker.emqx.io（须与车上 switcher_node 一致）。
- MQTT_BROKER_PORT：默认 1883。
- MQTT_ROBOT_ID：默认 robot01（须与车上 robot_id 参数一致）。

假定 uvicorn --workers 1，避免并发两条 MQTT 事务交错。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# 与 ros_ws switcher_node 对齐；vehicle_rooms 从该文件解析，此处保留送货校验用常量
try:
    from vehicle_rooms import (
        NON_DELIVERY_ROOM_IDS,
        ROOM_LOCATIONS,
        TOUR_ROOM_IDS,
    )

    ROOM_ALL = tuple(sorted(ROOM_LOCATIONS.keys()))
    DELIVERY_ROOM_IDS = TOUR_ROOM_IDS
except Exception:
    ROOM_ALL = (
        "100",
        "101",
        "102",
        "103",
        "104",
    )
    NON_DELIVERY_ROOM_IDS = ("100",)
    DELIVERY_ROOM_IDS = tuple(k for k in ROOM_ALL if k not in NON_DELIVERY_ROOM_IDS)


def bridge_enabled() -> bool:
    v = os.environ.get("MQTT_BRIDGE_ENABLED", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _broker_host() -> str:
    return os.environ.get("MQTT_BROKER_HOST", "broker.emqx.io").strip() or "broker.emqx.io"


def _broker_port() -> int:
    try:
        return int(os.environ.get("MQTT_BROKER_PORT", "1883"))
    except ValueError:
        return 1883


def _robot_id() -> str:
    return os.environ.get("MQTT_ROBOT_ID", "robot01").strip() or "robot01"


# switcher_node 订阅的 legacy 主题（与 robot/{id}/request 内 nav_room 不同）
TOPIC_NAV_ROOM_LEGACY = "robot/nav_room"
TOPIC_NAV_CANCEL_LEGACY = "robot/nav_cancel"


class RobotMqttBridge:
    """订阅 status、发布 request；带简单同步等待（单线程 RPC 锁）。"""

    def __init__(self) -> None:
        self._rid = _robot_id()
        self._topic_request = f"robot/{self._rid}/request"
        self._topic_status = f"robot/{self._rid}/status"
        self._topic_master = f"robot/{self._rid}/master/status"
        self._lock = threading.RLock()
        self._rpc_lock = threading.Lock()
        self._buf: list[dict[str, Any]] = []
        self._buf_max = 400
        self._last_hb: dict[str, Any] = {}
        self._last_master: dict[str, Any] = {}
        self._camera_stream_url: str = ""
        self._connected = threading.Event()
        self._client: Optional[mqtt.Client] = None
        self._on_task_status: Optional[Callable[[dict[str, Any]], None]] = None
        self._on_tour_arrived: Optional[Callable[[dict[str, Any]], None]] = None
        self._on_patrol_waypoint: Optional[Callable[[dict[str, Any]], None]] = None
        self._on_security_event: Optional[Callable[[dict[str, Any]], None]] = None

    def set_task_status_handler(self, fn: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self._on_task_status = fn

    def set_tour_arrived_handler(self, fn: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self._on_tour_arrived = fn

    def set_patrol_waypoint_handler(self, fn: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self._on_patrol_waypoint = fn

    def set_security_event_handler(self, fn: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self._on_security_event = fn

    def start(self) -> None:
        if not bridge_enabled():
            return
        with self._lock:
            if self._client is not None:
                return
            c = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION1)
            c.on_connect = self._on_connect
            c.on_message = self._on_message
            c.on_disconnect = self._on_disconnect
            try:
                c.connect(_broker_host(), _broker_port(), keepalive=60)
                c.loop_start()
            except Exception:
                try:
                    c.loop_stop()
                except Exception:
                    pass
                raise
            self._client = c

    def stop(self) -> None:
        with self._lock:
            c = self._client
            self._client = None
        self._connected.clear()
        if c is not None:
            try:
                c.loop_stop()
                c.disconnect()
            except Exception:
                pass

    def is_connected(self) -> bool:
        return self._connected.is_set() and self._client is not None

    def wait_connected(self, timeout: float = 15.0) -> bool:
        """等待 CONNACK；connect() 后 loop_start 异步建连，发布前须就绪。"""
        if self.is_connected():
            return True
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            if self.is_connected():
                return True
            time.sleep(0.05)
        return self.is_connected()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out = dict(self._last_hb)
            out.update(self._last_master)
            out["connected"] = self._connected.is_set()
            out["robot_id"] = self._rid
            out["broker"] = f"{_broker_host()}:{_broker_port()}"
            if self._camera_stream_url:
                out["camera_stream_url"] = self._camera_stream_url
            return out

    def delivery_ready_for_courier(self) -> bool:
        """与车上「可尾号校验」语义对齐：delivery_waiting 且 nav_state==IDLE。"""
        with self._lock:
            hb = dict(self._last_hb)
        if not hb:
            return False
        if not bool(hb.get("delivery_waiting", False)):
            return False
        return str(hb.get("nav_state", "")).strip() == "IDLE"

    @staticmethod
    def _connack_ok(rc: Any) -> bool:
        if rc is None:
            return True
        if hasattr(rc, "is_failure"):
            return not bool(rc.is_failure)
        try:
            return int(rc) == 0
        except (TypeError, ValueError):
            return True

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if not self._connack_ok(rc):
            return
        try:
            client.subscribe(self._topic_status, qos=1)
            client.subscribe(self._topic_master, qos=1)
        except Exception:
            pass
        self._connected.set()

    def _on_disconnect(self, client, userdata, rc, properties=None) -> None:
        self._connected.clear()

    def _on_message(self, client, userdata, msg) -> None:
        try:
            s = msg.payload.decode("utf-8", errors="replace")
            data = json.loads(s)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        mt = str(data.get("msg_type", "")).strip()
        with self._lock:
            if mt == "robot_heartbeat":
                self._last_hb = {
                    "nav_state": data.get("nav_state"),
                    "current_floor": data.get("current_floor"),
                    "current_goal_room": data.get("current_goal_room"),
                    "delivery_waiting": data.get("delivery_waiting"),
                    "elevator_pose_fallback": data.get("elevator_pose_fallback"),
                    "pose_x": data.get("pose_x"),
                    "pose_y": data.get("pose_y"),
                    "pose_yaw": data.get("pose_yaw"),
                    "current_map_yaml": data.get("current_map_yaml"),
                }
            if mt == "master_status" or msg.topic == self._topic_master:
                self._last_master = {
                    "master_mode": data.get("master_mode", "idle"),
                    "security_active": bool(data.get("security_active", False)),
                    "takeover_active": bool(data.get("takeover_active", False)),
                }
            self._buf.append(data)
            if len(self._buf) > self._buf_max:
                self._buf = self._buf[-self._buf_max // 2 :]
        if mt == "task_status" and self._on_task_status:
            try:
                self._on_task_status(data)
            except Exception:
                pass
        if mt == "tour_arrived" and self._on_tour_arrived:
            try:
                self._on_tour_arrived(data)
            except Exception:
                pass
        if mt in ("patrol_waypoint_done", "patrol_waypoint_failed") and self._on_patrol_waypoint:
            try:
                self._on_patrol_waypoint(data)
            except Exception:
                pass
        if mt in ("security_person_event", "patrol_track_status", "guard_status") and self._on_security_event:
            try:
                self._on_security_event(data)
            except Exception:
                pass
        if mt == "patrol_camera_stream":
            with self._lock:
                url = str(data.get("stream_url", "") or "").strip()
                if url:
                    self._camera_stream_url = url

    def _publish(self, obj: dict[str, Any]) -> None:
        c = self._client
        if c is None:
            raise RuntimeError(
                "MQTT 未初始化：后端启动时连 broker 失败。"
                "请检查 MQTT_BROKER_HOST/PORT 与网络（1883），重启 backend。"
            )
        if not self.wait_connected(timeout=15.0):
            host, port = _broker_host(), _broker_port()
            raise RuntimeError(
                f"MQTT 未连接 broker {host}:{port}（publish rc=4 即未建连）。"
                f"请确认：1) 本机可访问 {host}:{port}；"
                f"2) 车端 smart_switcher 已启动且 robot_id={self._rid}；"
                f"3) GET /api/bridge/status 中 connected 为 true 后再进入巡逻。"
            )
        payload = json.dumps(obj, ensure_ascii=False)
        inf = c.publish(self._topic_request, payload, qos=1)
        rc = getattr(inf, "rc", None)
        if rc is not None and rc != mqtt.MQTT_ERR_SUCCESS:
            hint = "未连接" if int(rc) == mqtt.MQTT_ERR_NO_CONN else f"rc={rc}"
            raise RuntimeError(f"MQTT publish 失败 {hint}")

    def _wait(self, pred: Callable[[dict[str, Any]], bool], timeout: float) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for i in range(len(self._buf) - 1, -1, -1):
                    m = self._buf[i]
                    try:
                        if pred(m):
                            self._buf.pop(i)
                            return m
                    except Exception:
                        continue
            time.sleep(0.04)
        return None

    def wait_pickup_ack(self, request_id: str, timeout: float = 12.0) -> dict[str, Any]:
        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "pickup_ack":
                return False
            return str(m.get("request_id", "")).strip() == request_id

        r = self._wait(pred, timeout)
        if r is None:
            return {"ok": False, "reason": "等待 pickup_ack 超时（车端未响应或 MQTT 未连通）"}
        return r

    def publish_pickup_request(self, request_id: str, phone_tail: str, location: str) -> dict[str, Any]:
        rid = request_id.strip()
        with self._rpc_lock:
            self._publish(
                {
                    "msg_type": "pickup_request",
                    "request_id": rid,
                    "phone_tail": phone_tail.strip(),
                    "location": location.strip(),
                }
            )
        return self.wait_pickup_ack(rid)

    def wait_courier_check(self, phone_tail: str, request_id: str, timeout: float = 15.0) -> dict[str, Any]:
        rid = request_id.strip()
        tail = phone_tail.strip()

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "courier_check_result":
                return False
            if str(m.get("phone_tail", "")).strip() != tail:
                return False
            if not m.get("ok"):
                return True
            return str(m.get("request_id", "")).strip() == rid

        r = self._wait(pred, timeout)
        if r is None:
            return {"ok": False, "reason": "等待 courier_check_result 超时"}
        return r

    def publish_courier_dropoff(self, phone_tail: str, request_id: str) -> dict[str, Any]:
        tail, rid = phone_tail.strip(), request_id.strip()
        with self._rpc_lock:
            self._publish(
                {
                    "msg_type": "courier_dropoff",
                    "phone_tail": tail,
                    "request_id": rid,
                }
            )
        return self.wait_courier_check(tail, rid)

    def wait_delivery_start(self, request_id: str, timeout: float = 15.0) -> dict[str, Any]:
        rid = request_id.strip()

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "delivery_start_result":
                return False
            if str(m.get("request_id", "")).strip() != rid:
                return False
            return True

        r = self._wait(pred, timeout)
        if r is None:
            return {"ok": False, "reason": "等待 delivery_start_result 超时"}
        return r

    def publish_confirm_delivery(self, phone_tail: str, request_id: str) -> dict[str, Any]:
        tail, rid = phone_tail.strip(), request_id.strip()
        with self._rpc_lock:
            self._publish(
                {
                    "msg_type": "confirm_delivery",
                    "phone_tail": tail,
                    "request_id": rid,
                }
            )
        return self.wait_delivery_start(rid)

    def wait_confirm_receipt(self, request_id: str, timeout: float = 20.0) -> dict[str, Any]:
        rid = request_id.strip()

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "confirm_receipt_result":
                return False
            return str(m.get("request_id", "")).strip() == rid

        r = self._wait(pred, timeout)
        if r is None:
            return {"ok": False, "reason": "等待 confirm_receipt_result 超时"}
        return r

    def publish_confirm_receipt(self, request_id: str) -> dict[str, Any]:
        rid = request_id.strip()
        with self._rpc_lock:
            self._publish({"msg_type": "confirm_receipt", "request_id": rid})
        return self.wait_confirm_receipt(rid)

    def publish_clear_tasks(self) -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "clear_tasks"})

    def publish_tour_nav(self, tour_id: str, room: str) -> dict[str, Any]:
        tid, loc = tour_id.strip(), room.strip()
        with self._rpc_lock:
            self._publish({"msg_type": "tour_nav", "tour_id": tid, "room": loc})
        return self.wait_tour_nav_result(tid)

    def wait_tour_nav_result(self, tour_id: str, timeout: float = 15.0) -> dict[str, Any]:
        tid = tour_id.strip()

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "tour_nav_result":
                return False
            return str(m.get("tour_id", "")).strip() == tid

        r = self._wait(pred, timeout)
        if r is None:
            return {"ok": False, "reason": "等待 tour_nav_result 超时"}
        return r

    def wait_tour_arrived(self, tour_id: str, timeout: float = 300.0) -> Optional[dict[str, Any]]:
        tid = tour_id.strip()

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "tour_arrived":
                return False
            return str(m.get("tour_id", "")).strip() == tid

        return self._wait(pred, timeout)

    def peek_tour_arrived(self, tour_id: str) -> Optional[dict[str, Any]]:
        tid = tour_id.strip()
        with self._lock:
            for i in range(len(self._buf) - 1, -1, -1):
                m = self._buf[i]
                if m.get("msg_type") != "tour_arrived":
                    continue
                if str(m.get("tour_id", "")).strip() == tid:
                    self._buf.pop(i)
                    return m
        return None

    def heartbeat_tour_arrived(self, target_room: str) -> bool:
        """备用：nav_state=IDLE 且 current_goal_room 匹配目标。"""
        room = target_room.strip()
        with self._lock:
            hb = dict(self._last_hb)
        if not hb:
            return False
        if str(hb.get("nav_state", "")).strip() != "IDLE":
            return False
        return str(hb.get("current_goal_room", "")).strip() == room

    def publish_tour_cancel(self, tour_id: str) -> None:
        """Deprecated alias: 原地截停（请优先用 publish_tour_stop_in_place）。"""
        self.publish_tour_stop_in_place(tour_id)

    def publish_tour_stop_in_place(self, tour_id: str) -> None:
        with self._rpc_lock:
            self._publish(
                {"msg_type": "tour_stop_in_place", "tour_id": tour_id.strip()}
            )

    def publish_nav_cancel(self, reason: str = "backend") -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "nav_cancel", "reason": reason})

    def publish_nav_room(self, room: str) -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "nav_room", "room": str(room).strip()})

    def _publish_raw_topic(self, topic: str, payload: str) -> None:
        """发布到任意 MQTT 主题（保安 legacy nav 用；送货/导览仍走 _publish/request）。"""
        c = self._client
        if c is None:
            raise RuntimeError("MQTT 未初始化")
        if not self.wait_connected(timeout=15.0):
            raise RuntimeError(f"MQTT 未连接，无法发布到 {topic}")
        inf = c.publish(topic, payload, qos=1)
        rc = getattr(inf, "rc", None)
        if rc is not None and rc != mqtt.MQTT_ERR_SUCCESS:
            hint = "未连接" if int(rc) == mqtt.MQTT_ERR_NO_CONN else f"rc={rc}"
            raise RuntimeError(f"MQTT publish 到 {topic} 失败 {hint}")

    def publish_nav_cancel_legacy(self, reason: str = "backend") -> None:
        """保安专用：switcher 在 robot/nav_cancel 上处理截停。"""
        with self._rpc_lock:
            self._publish_raw_topic(
                TOPIC_NAV_CANCEL_LEGACY,
                json.dumps({"reason": reason}, ensure_ascii=False),
            )

    def publish_nav_room_legacy(self, room: str) -> None:
        """保安专用：switcher 在 robot/nav_room 上处理房间导航（与终端 mosquitto_pub 一致）。"""
        with self._rpc_lock:
            self._publish_raw_topic(
                TOPIC_NAV_ROOM_LEGACY,
                json.dumps({"room": str(room).strip()}, ensure_ascii=False),
            )

    def publish_patrol_master_status(self, payload: dict[str, Any]) -> None:
        """PC 巡逻模式状态同步（供车载/onboard 读取 master/status）。"""
        with self._rpc_lock:
            body = {"msg_type": "master_status", **payload}
            c = self._client
            if c is None:
                return
            try:
                c.publish(self._topic_master, json.dumps(body, ensure_ascii=False), qos=1)
            except Exception:
                pass

    def publish_patrol_nav_waypoint(
        self,
        *,
        request_id: str,
        patrol_epoch: int,
        floor: str,
        index: int,
        label: str,
        x: float,
        y: float,
        yaw: float,
        action: str,
        map_yaml: str,
    ) -> None:
        with self._rpc_lock:
            self._publish(
                {
                    "msg_type": "patrol_nav_waypoint",
                    "request_id": request_id,
                    "patrol_epoch": int(patrol_epoch),
                    "floor": floor,
                    "index": int(index),
                    "label": label,
                    "x": float(x),
                    "y": float(y),
                    "yaw": float(yaw),
                    "action": action,
                    "map_yaml": map_yaml,
                }
            )

    def publish_patrol_motion_mode(self, mode: str) -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "patrol_motion_mode", "mode": str(mode).strip()})

    def publish_patrol_track_start(
        self,
        *,
        request_id: str,
        patrol_epoch: int,
        resume_index: int,
        resume_label: str,
        max_linear_mps: float = 0.15,
        goal_update_hz: float = 5.0,
    ) -> None:
        with self._rpc_lock:
            self._publish(
                {
                    "msg_type": "patrol_track_start",
                    "request_id": request_id,
                    "patrol_epoch": int(patrol_epoch),
                    "resume_index": int(resume_index),
                    "resume_label": resume_label,
                    "max_linear_mps": float(max_linear_mps),
                    "goal_update_hz": float(goal_update_hz),
                }
            )

    def publish_patrol_track_stop(self, reason: str = "manual") -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "patrol_track_stop", "reason": str(reason)})

    def publish_guard_rotate(self, delta_deg: float) -> None:
        with self._rpc_lock:
            self._publish(
                {
                    "msg_type": "guard_rotate",
                    "delta_deg": float(delta_deg),
                    "patrol_epoch": 0,
                }
            )

    def publish_guard_rotate_cancel(self) -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "guard_rotate_cancel"})

    def publish_patrol_vision_config(
        self,
        *,
        conf: float | None = None,
        guard_view_track_enabled: bool | None = None,
        patrol_track_enabled: bool | None = None,
    ) -> None:
        payload: dict[str, object] = {"msg_type": "patrol_vision_config"}
        if conf is not None:
            payload["conf"] = float(conf)
        if guard_view_track_enabled is not None:
            payload["guard_view_track_enabled"] = bool(guard_view_track_enabled)
        if patrol_track_enabled is not None:
            payload["patrol_track_enabled"] = bool(patrol_track_enabled)
        with self._rpc_lock:
            self._publish(payload)

    def publish_delivery_return_home(self) -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "return_home"})

    def publish_tour_return_home(self, tour_id: str) -> None:
        with self._rpc_lock:
            self._publish({"msg_type": "tour_return_home", "tour_id": tour_id.strip()})

    def wait_tour_return_result(self, tour_id: str, timeout: float = 15.0) -> dict[str, Any]:
        tid = tour_id.strip()

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "tour_return_result":
                return False
            return str(m.get("tour_id", "")).strip() == tid

        r = self._wait(pred, timeout)
        if r is None:
            return {"ok": False, "reason": "等待 tour_return_result 超时"}
        return r

    def wait_tour_return_complete(self, tour_id: str, timeout: float = 300.0) -> Optional[dict[str, Any]]:
        tid = tour_id.strip()

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "tour_return_complete":
                return False
            return str(m.get("tour_id", "")).strip() == tid

        return self._wait(pred, timeout)

    def peek_tour_return_complete(self, tour_id: str) -> Optional[dict[str, Any]]:
        tid = tour_id.strip()
        with self._lock:
            for i in range(len(self._buf) - 1, -1, -1):
                m = self._buf[i]
                if m.get("msg_type") != "tour_return_complete":
                    continue
                if str(m.get("tour_id", "")).strip() == tid:
                    self._buf.pop(i)
                    return m
        return None

    def heartbeat_at_entry_idle(self) -> bool:
        from vehicle_rooms import ENTRY_ROOM_ID

        with self._lock:
            hb = dict(self._last_hb)
        if not hb:
            return False
        if str(hb.get("nav_state", "")).strip() != "IDLE":
            return False
        if not bool(hb.get("delivery_waiting", False)):
            return False
        gr = str(hb.get("current_goal_room", "")).strip()
        return gr in (ENTRY_ROOM_ID, "100", "")

    def wait_idle_delivery_waiting(self, timeout: float = 180.0) -> bool:
        """回点后 delivery_waiting==True 且 nav_state==IDLE（小车再次可投件）。"""

        def pred(m: dict[str, Any]) -> bool:
            if m.get("msg_type") != "robot_heartbeat":
                return False
            if not bool(m.get("delivery_waiting", False)):
                return False
            return str(m.get("nav_state", "")).strip() == "IDLE"

        r = self._wait(pred, timeout)
        return r is not None


_bridge_singleton: Optional[RobotMqttBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> RobotMqttBridge:
    global _bridge_singleton
    with _bridge_lock:
        if _bridge_singleton is None:
            _bridge_singleton = RobotMqttBridge()
        return _bridge_singleton
