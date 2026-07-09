import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt
import rclpy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from nav2_msgs.srv import ClearEntireCostmap, LoadMap
from paho.mqtt.enums import CallbackAPIVersion
from rclpy.action import ActionClient
from rclpy.node import Node


# ================= ⚠️ 全局配置区域 (已更新为最新坐标) =================

MAP_BASE_DIR = "/home/rock/Desktop/rock_ws/ros_ws/install/rt_robot_nav2/share/rt_robot_nav2/map/"

FLOOR_MAPS = {
    "1F": "my_map3.yaml",
    "2F": "my_map.yaml",
}

# 注意：initial_pose 为该楼层出电梯后的重定位点（与 entry 可相同）
ELEVATOR_POSITIONS = {
    "1F": {
        "entry": {"x": 0.693, "y": 5.5, "yaw": 0.0},
        "initial_pose": {"x": 0.693, "y": 5.5, "yaw": 0.0},
    },
    "2F": {
        "entry": {"x": 0.00687, "y": 0.0118, "yaw": 0.0132},
        "initial_pose": {"x": 0.00687, "y": 0.0118, "yaw": 0.0132},
    },
}

ROOM_LOCATIONS = {
    "100": {"floor": "1F", "x": -0.254, "y": 0.551, "yaw": 0.0},
    "101": {"floor": "1F", "x": 3.73, "y": 7.94, "yaw": 0.0},
    "102": {"floor": "1F", "x": -3.24, "y": 7.32, "yaw": 0.0},
    "103": {"floor": "1F", "x": -0.235, "y": 10.4, "yaw": 0.0},
    "104": {"floor": "1F", "x": -2.79, "y": 3.86, "yaw": 0.0},
    "200": {"floor": "2F", "x": 0.00687, "y": 0.0118, "yaw": 0.0},
    "201": {"floor": "2F", "x": 6.03, "y": -0.1, "yaw": 0.0},
    "202": {"floor": "2F", "x": 0.629, "y": 5.09, "yaw": 0.0},
    "203": {"floor": "2F", "x": 6.7, "y": 2.68, "yaw": 0.0},
    "204": {"floor": "2F", "x": 2.41, "y": 6.78, "yaw": 0.0},
}

# 100=1F 出入口；200=2F 电梯参考点，不作为用户下单配送点
NON_DELIVERY_ROOM_IDS = ("100", "200")
DELIVERY_ROOM_IDS = tuple(k for k in ROOM_LOCATIONS if k not in NON_DELIVERY_ROOM_IDS)
ENTRY_ROOM_ID = "100"

MQTT_BROKER = "broker.emqx.io"
TOPIC_ELEV_REQ = "elevator/request"
TOPIC_ELEV_RESP = "elevator/response"

# 兼容旧调试：仍订阅 raw 房间指令（可选）
TOPIC_NAV_ROOM_LEGACY = "robot/nav_room"


# =============================================================================
@dataclass
class DeliveryTask:
    request_id: str
    phone_tail: str
    location: str
    status: str = "queued"
    created_ts: int = field(default_factory=lambda: int(time.time()))


class TaskQueue:
    """与 Android / delivery_mqtt_system 对齐的内存任务队列（无持久化）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Optional[DeliveryTask] = None
        self._queue: list[DeliveryTask] = []

    def create_task(self, request_id: str, phone_tail: str, location: str) -> DeliveryTask:
        t = DeliveryTask(request_id=request_id.strip(), phone_tail=phone_tail.strip(), location=location.strip())
        with self._lock:
            self._queue.append(t)
        return t

    def get_current(self) -> Optional[DeliveryTask]:
        with self._lock:
            return self._current

    def get_by_tail(self, phone_tail: str) -> list[DeliveryTask]:
        with self._lock:
            n = phone_tail.strip()
            out: list[DeliveryTask] = []
            if self._current and self._current.phone_tail == n:
                out.append(self._current)
            for t in self._queue:
                if t.phone_tail == n:
                    out.append(t)
            return out

    def activate_for_dropoff(self, request_id: str) -> Optional[DeliveryTask]:
        with self._lock:
            if self._current and self._current.request_id == request_id:
                self._current.status = "waiting_dropoff"
                return self._current
            idx = next((i for i, t in enumerate(self._queue) if t.request_id == request_id), -1)
            if idx < 0:
                return None
            chosen = self._queue.pop(idx)
            chosen.status = "waiting_dropoff"
            if self._current:
                self._current.status = "queued"
                self._queue.insert(0, self._current)
            self._current = chosen
            return self._current

    def prioritize_by_request_id(self, request_id: str) -> Optional[DeliveryTask]:
        with self._lock:
            if self._current and self._current.request_id == request_id:
                return self._current
            idx = next((i for i, t in enumerate(self._queue) if t.request_id == request_id), -1)
            if idx < 0:
                return None
            chosen = self._queue.pop(idx)
            if self._current:
                self._current.status = "queued"
                self._queue.insert(0, self._current)
            self._current = chosen
            return self._current

    def update_status(self, request_id: str, status: str) -> bool:
        with self._lock:
            if self._current and self._current.request_id == request_id:
                self._current.status = status
                return True
            for t in self._queue:
                if t.request_id == request_id:
                    t.status = status
                    return True
        return False

    def clear_all(self) -> int:
        with self._lock:
            n = len(self._queue) + (1 if self._current else 0)
            self._current = None
            self._queue = []
            return n

    def complete_current(self) -> Optional[DeliveryTask]:
        with self._lock:
            if not self._current:
                return None
            self._current.status = "completed"
            done = self._current
            self._current = None
            return done

    def fail_current(self) -> Optional[DeliveryTask]:
        with self._lock:
            if not self._current:
                return None
            self._current.status = "failed"
            failed = self._current
            self._current = None
            return failed

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            items: list[dict[str, Any]] = []
            if self._current:
                items.append(
                    {
                        "request_id": self._current.request_id,
                        "phone_tail": self._current.phone_tail,
                        "location": self._current.location,
                        "status": self._current.status,
                        "created_ts": self._current.created_ts,
                        "is_current": True,
                    }
                )
            for t in self._queue:
                items.append(
                    {
                        "request_id": t.request_id,
                        "phone_tail": t.phone_tail,
                        "location": t.location,
                        "status": t.status,
                        "created_ts": t.created_ts,
                        "is_current": False,
                    }
                )
            return items


class SmartBuildingNavigator(Node):
    """
    统一 Android 端 MQTT（robot/{id}/request|status）与 Nav2/电梯逻辑。
    流程：默认在 100 待命 → 用户 pickup → 送货员尾号+确认送货 → 导航房间 → 用户 confirm_receipt → 回 100。
    """

    def __init__(self) -> None:
        super().__init__("smart_building_navigator")

        self.declare_parameter("robot_id", "robot01")
        rid = str(self.get_parameter("robot_id").get_parameter_value().string_value or "robot01").strip() or "robot01"
        self._topic_request = f"robot/{rid}/request"
        self._topic_status = f"robot/{rid}/status"

        self._tasks = TaskQueue()
        self._state_lock = threading.Lock()

        self.current_floor = "1F"
        self.state = "IDLE"
        self.current_goal_room: Optional[str] = None
        self.target_next_floor: Optional[str] = None
        self.delivery_waiting = True
        self._dev_floor_only = False

        self._nav_context: str = ""
        self._xf_origin_context: str = ""

        self.mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION1)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        try:
            self.mqtt_client.connect(MQTT_BROKER, 1883, 60)
            self.mqtt_client.loop_start()
            self.get_logger().info(f"MQTT Connected, request={self._topic_request}")
        except Exception as e:
            self.get_logger().error(f"MQTT Error: {e}")

        self.load_map_client = self.create_client(LoadMap, "/map_server/load_map")
        self.nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self.cancel_client = self.create_client(CancelGoal, "/navigate_to_pose/_action/cancel_goal")
        self.clear_global_costmap_client = self.create_client(ClearEntireCostmap, "/global_costmap/clear_entire_costmap")
        self.clear_local_costmap_client = self.create_client(ClearEntireCostmap, "/local_costmap/clear_entire_costmap")

        self.pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self.pose_callback, 10
        )
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self.current_pos = None

        self.status_timer = threading.Thread(target=self.publish_status_loop, daemon=True)
        self.status_timer.start()

        self.get_logger().info(f"Initialized floor={self.current_floor}, topics {self._topic_request}")

    # --- MQTT ---
    def on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        client.subscribe(self._topic_request)
        client.subscribe(TOPIC_ELEV_RESP)
        client.subscribe(TOPIC_NAV_ROOM_LEGACY)

    def on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        payload_str = msg.payload.decode()
        try:
            if topic == TOPIC_ELEV_RESP:
                self.handle_elevator_response(payload_str)
                return
            if topic == TOPIC_NAV_ROOM_LEGACY:
                self.handle_legacy_nav_room(payload_str)
                return
            data = json.loads(payload_str)
            self.handle_app_request(data)
        except json.JSONDecodeError:
            self.get_logger().warn(f"Bad JSON on {topic}")
        except Exception as e:
            self.get_logger().error(f"MQTT handle error: {e}")

    def _publish(self, obj: dict) -> None:
        self.mqtt_client.publish(self._topic_status, json.dumps(obj, ensure_ascii=False))

    def _publish_queue_snapshot(self) -> None:
        self._publish({"msg_type": "queue_snapshot", "queue_size": len(self._tasks.snapshot()), "items": self._tasks.snapshot()})

    def _publish_task_status(self, request_id: str, status: str, reason: str = "") -> None:
        payload: dict[str, Any] = {
            "msg_type": "task_status",
            "request_id": request_id,
            "status": status,
            "ts": int(time.time()),
        }
        if reason:
            payload["reason"] = reason
        self._publish(payload)

    def _publish_robot_heartbeat(self) -> None:
        with self._state_lock:
            st = self.state
            df = self.delivery_waiting
            fl = self.current_floor
            gr = self.current_goal_room
        self._publish(
            {
                "msg_type": "robot_heartbeat",
                "nav_state": st,
                "current_floor": fl,
                "current_goal_room": gr,
                "delivery_waiting": df,
            }
        )

    def publish_status_loop(self):
        while rclpy.ok():
            self._publish_robot_heartbeat()
            self._publish_queue_snapshot()
            time.sleep(3.0)

    # --- 应用协议 ---
    def handle_app_request(self, data: dict) -> None:
        mt = str(data.get("msg_type", "")).strip()
        if mt == "pickup_request":
            self._handle_pickup_request(data)
        elif mt == "courier_dropoff":
            self._handle_courier_dropoff(data)
        elif mt == "confirm_delivery":
            self._handle_confirm_delivery(data)
        elif mt == "confirm_receipt":
            self._handle_confirm_receipt(data)
        elif mt == "clear_tasks":
            self._handle_clear_tasks(data)
        elif mt == "dev_switch_floor":
            self._handle_dev_switch_floor(data)
        else:
            self.get_logger().debug(f"Ignored msg_type={mt}")

        self._publish_queue_snapshot()

    def _handle_pickup_request(self, msg: dict) -> None:
        required = ("request_id", "phone_tail", "location")
        if not all(msg.get(k) for k in required):
            self._publish(
                {
                    "msg_type": "pickup_ack",
                    "ok": False,
                    "reason": "缺少 request_id / phone_tail / location",
                }
            )
            return
        loc = str(msg["location"]).strip()
        if loc not in DELIVERY_ROOM_IDS:
            self._publish(
                {
                    "msg_type": "pickup_ack",
                    "ok": False,
                    "reason": f"房间号必须是以下之一: {', '.join(DELIVERY_ROOM_IDS)}",
                }
            )
            return
        task = self._tasks.create_task(str(msg["request_id"]), str(msg["phone_tail"]), loc)
        self._publish(
            {
                "msg_type": "pickup_ack",
                "ok": True,
                "request_id": task.request_id,
                "status": task.status,
                "queue_size": len(self._tasks.snapshot()),
            }
        )

    def _handle_courier_dropoff(self, msg: dict) -> None:
        phone_tail = str(msg.get("phone_tail", "")).strip()
        request_id = str(msg.get("request_id", "")).strip()
        if not phone_tail:
            self._publish({"msg_type": "courier_check_result", "ok": False, "reason": "手机号后4位不能为空"})
            return

        with self._state_lock:
            dw = self.delivery_waiting
            st = self.state

        if not dw or st != "IDLE":
            self._publish(
                {
                    "msg_type": "courier_check_result",
                    "ok": False,
                    "phone_tail": phone_tail,
                    "reason": "小车未在出入口待命(送货等待状态)，请稍后再试",
                }
            )
            return

        candidates = self._tasks.get_by_tail(phone_tail)
        if not candidates:
            self._publish(
                {
                    "msg_type": "courier_check_result",
                    "ok": False,
                    "phone_tail": phone_tail,
                    "reason": "当前号码不存在，请确认用户已发取货请求",
                }
            )
            return

        if request_id:
            act = self._tasks.activate_for_dropoff(request_id)
            if not act:
                self._publish(
                    {
                        "msg_type": "courier_check_result",
                        "ok": False,
                        "phone_tail": phone_tail,
                        "reason": "请求已变化或不存在，请重新校验",
                    }
                )
                return
            self._publish_task_status(act.request_id, "waiting_dropoff")
            self._publish(
                {
                    "msg_type": "courier_check_result",
                    "ok": True,
                    "phone_tail": phone_tail,
                    "request_id": act.request_id,
                    "location": act.location,
                    "hint": "请放货后点击确认送货",
                }
            )
            return

        if len(candidates) > 1:
            options = [
                {
                    "request_id": t.request_id,
                    "location": t.location,
                    "created_ts": t.created_ts,
                }
                for t in sorted(candidates, key=lambda x: x.created_ts, reverse=True)
            ]
            self._publish(
                {
                    "msg_type": "courier_check_result",
                    "ok": False,
                    "phone_tail": phone_tail,
                    "reason": "同尾号多个任务，请选择具体任务",
                    "need_select": True,
                    "candidates": options,
                }
            )
            return

        act = self._tasks.activate_for_dropoff(candidates[0].request_id)
        if act:
            self._publish_task_status(act.request_id, "waiting_dropoff")
        self._publish(
            {
                "msg_type": "courier_check_result",
                "ok": True,
                "phone_tail": phone_tail,
                "request_id": candidates[0].request_id,
                "location": candidates[0].location,
                "hint": "请放货后点击确认送货",
            }
        )

    def _handle_confirm_delivery(self, msg: dict) -> None:
        phone_tail = str(msg.get("phone_tail", "")).strip()
        request_id = str(msg.get("request_id", "")).strip()

        with self._state_lock:
            dw = self.delivery_waiting
            st = self.state

        if not dw or st != "IDLE":
            self._publish(
                {
                    "msg_type": "delivery_start_result",
                    "ok": False,
                    "reason": "小车未在出入口待命，无法开始送货",
                }
            )
            return

        cur = self._tasks.get_current()
        if cur is None:
            self._publish({"msg_type": "delivery_start_result", "ok": False, "reason": "当前没有可配送任务"})
            return

        if request_id and cur.request_id != request_id:
            promoted = self._tasks.prioritize_by_request_id(request_id)
            if not promoted:
                self._publish({"msg_type": "delivery_start_result", "ok": False, "reason": "未找到对应任务"})
                return
            cur = promoted

        if phone_tail and cur.phone_tail != phone_tail:
            self._publish({"msg_type": "delivery_start_result", "ok": False, "reason": "尾号与当前任务不一致"})
            return

        if cur.status != "waiting_dropoff":
            self._publish(
                {
                    "msg_type": "delivery_start_result",
                    "ok": False,
                    "request_id": cur.request_id,
                    "reason": f"当前任务状态为 {cur.status}，不能开始配送",
                }
            )
            return

        room_id = cur.location
        if room_id not in ROOM_LOCATIONS:
            self._publish({"msg_type": "delivery_start_result", "ok": False, "reason": f"无效房间 {room_id}"})
            return

        self._tasks.update_status(cur.request_id, "delivering")
        self._publish_task_status(cur.request_id, "delivering")

        with self._state_lock:
            self.delivery_waiting = False

        self._publish({"msg_type": "delivery_start_result", "ok": True, "request_id": cur.request_id})
        self._begin_navigate_to_room(room_id, nav_context="deliver_room")

    def _handle_confirm_receipt(self, msg: dict) -> None:
        request_id = str(msg.get("request_id", "")).strip()
        if not request_id:
            self._publish({"msg_type": "confirm_receipt_result", "ok": False, "reason": "缺少 request_id"})
            return

        cur = self._tasks.get_current()
        if not cur or cur.request_id != request_id:
            self._publish({"msg_type": "confirm_receipt_result", "ok": False, "reason": "没有匹配的当前任务"})
            return

        if cur.status != "waiting_receipt":
            self._publish(
                {
                    "msg_type": "confirm_receipt_result",
                    "ok": False,
                    "request_id": request_id,
                    "reason": f"任务状态为 {cur.status}，无法确认收货",
                }
            )
            return

        with self._state_lock:
            st = self.state
        if st != "IDLE":
            self._publish({"msg_type": "confirm_receipt_result", "ok": False, "reason": f"小车忙({st})，请稍候"})
            return

        self._tasks.update_status(request_id, "returning_to_entry")
        self._publish_task_status(request_id, "returning_to_entry")

        with self._state_lock:
            self.delivery_waiting = False

        self._publish({"msg_type": "confirm_receipt_result", "ok": True, "request_id": request_id})
        self._begin_navigate_to_room(ENTRY_ROOM_ID, nav_context="return_home")

    def _handle_clear_tasks(self, msg: dict) -> None:
        n = self._tasks.clear_all()
        self.cancel_nav()
        with self._state_lock:
            self.state = "IDLE"
            self.current_goal_room = None
            self.target_next_floor = None
            self._dev_floor_only = False
            self.delivery_waiting = True
            self._nav_context = ""
            self._xf_origin_context = ""
        self._publish({"msg_type": "clear_tasks_result", "ok": True, "cleared_count": n, "reason": "已清空"})

    def _handle_dev_switch_floor(self, msg: dict) -> None:
        target = str(msg.get("target_floor", "")).strip()
        if target not in FLOOR_MAPS:
            self._publish(
                {
                    "msg_type": "dev_switch_floor_result",
                    "ok": False,
                    "reason": f"target_floor 必须是 {list(FLOOR_MAPS.keys())}",
                }
            )
            return

        with self._state_lock:
            st = self.state
            cf = self.current_floor

        if st != "IDLE":
            self._publish({"msg_type": "dev_switch_floor_result", "ok": False, "reason": f"小车忙({st})，仅 IDLE 可调试跨层"})
            return

        if target == cf:
            self._publish({"msg_type": "dev_switch_floor_result", "ok": True, "current_floor": cf, "note": "已在目标楼层"})
            return

        with self._state_lock:
            self._dev_floor_only = True
            self.target_next_floor = target
            self.delivery_waiting = False

        self.get_logger().info(f"dev_switch_floor: {cf} -> {target}")
        self._go_to_elevator(nav_context="dev_elevator")

    def handle_legacy_nav_room(self, payload_str: str) -> None:
        try:
            if payload_str.startswith("{"):
                data = json.loads(payload_str)
                room_id = str(data.get("room", ""))
            else:
                room_id = payload_str.strip()
        except Exception:
            return
        if room_id in ROOM_LOCATIONS:
            self._begin_navigate_to_room(room_id, nav_context="legacy")

    # --- 导航 ---
    def cancel_nav(self) -> None:
        if self.cancel_client.service_is_ready():
            self.cancel_client.call_async(CancelGoal.Request())

    def _begin_navigate_to_room(self, room_id: str, nav_context: str) -> None:
        if room_id not in ROOM_LOCATIONS:
            self.get_logger().error(f"Unknown room {room_id}")
            return

        target_info = ROOM_LOCATIONS[room_id]
        target_floor = target_info["floor"]

        with self._state_lock:
            self.current_goal_room = room_id
            self._nav_context = nav_context

        if target_floor == self.current_floor:
            self.get_logger().info(f"Navigate same floor -> {room_id}")
            with self._state_lock:
                self.state = "NAVIGATING_TO_ROOM"
                self._nav_context = nav_context
            self.send_nav_goal_tracked(target_info["x"], target_info["y"], target_info["yaw"])
            return

        self.get_logger().info(f"Cross floor -> elevator then {room_id}")
        with self._state_lock:
            self.target_next_floor = target_floor
            self._xf_origin_context = nav_context
        self._go_to_elevator(nav_context=nav_context + "_xf")

    def _go_to_elevator(self, nav_context: str) -> None:
        with self._state_lock:
            self.state = "GOING_TO_ELEVATOR"
            self._nav_context = nav_context
        elev = ELEVATOR_POSITIONS[self.current_floor]["entry"]
        self.send_nav_goal_tracked(elev["x"], elev["y"], elev["yaw"])

    def pose_callback(self, msg) -> None:
        self.current_pos = msg.pose.pose
        with self._state_lock:
            st = self.state
        if st != "GOING_TO_ELEVATOR" or not self.current_pos:
            return
        elev_entry = ELEVATOR_POSITIONS[self.current_floor]["entry"]
        dist = math.sqrt(
            (self.current_pos.position.x - elev_entry["x"]) ** 2 + (self.current_pos.position.y - elev_entry["y"]) ** 2
        )
        if dist < 1.0:
            self.get_logger().info("Pose near elevator (fallback)")
            self.request_elevator()

    def request_elevator(self) -> None:
        with self._state_lock:
            if self.state != "GOING_TO_ELEVATOR":
                return
            self.state = "WAITING_ELEVATOR"
            tgt = self.target_next_floor

        self.cancel_nav()
        req_payload = {"current_floor": self.current_floor, "target_floor": tgt, "action": "call"}
        self.mqtt_client.publish(TOPIC_ELEV_REQ, json.dumps(req_payload))
        self.get_logger().info("Elevator request sent")

    def handle_elevator_response(self, payload_str: str) -> None:
        with self._state_lock:
            st = self.state
        if st != "WAITING_ELEVATOR":
            return
        try:
            data = json.loads(payload_str)
            if data.get("status") == "arrived":
                with self._state_lock:
                    tf = self.target_next_floor
                if tf:
                    self.switch_floor_map(tf)
        except Exception:
            pass

    def switch_floor_map(self, new_floor: str) -> None:
        with self._state_lock:
            self.state = "SWITCHING_MAP"

        map_file = FLOOR_MAPS.get(new_floor)
        if not map_file:
            self.get_logger().error(f"No map for {new_floor}")
            with self._state_lock:
                self.state = "IDLE"
            return

        req = LoadMap.Request()
        req.map_url = MAP_BASE_DIR + map_file
        fut = self.load_map_client.call_async(req)
        fut.add_done_callback(lambda f: self.on_map_loaded(f, new_floor))

    def on_map_loaded(self, future, new_floor: str) -> None:
        try:
            resp = future.result()
            if resp.result != LoadMap.Response().RESULT_SUCCESS:
                self.get_logger().error("Map load failed")
                with self._state_lock:
                    self.state = "IDLE"
                    self._dev_floor_only = False
                return

            self.get_logger().info(f"Map {new_floor} loaded")
            relocal_pose = ELEVATOR_POSITIONS[new_floor]["initial_pose"]
            self.force_relocalize(relocal_pose)

            self.get_logger().info("Waiting for costmap...")
            time.sleep(3.0)

            if self.clear_global_costmap_client.service_is_ready():
                self.clear_global_costmap_client.call_async(ClearEntireCostmap.Request())
            if self.clear_local_costmap_client.service_is_ready():
                self.clear_local_costmap_client.call_async(ClearEntireCostmap.Request())
            time.sleep(1.0)

            with self._state_lock:
                self.current_floor = new_floor
                dev_only = self._dev_floor_only
                goal_room = self.current_goal_room

            wake_x = relocal_pose["x"] + 0.5 * math.cos(relocal_pose["yaw"])
            wake_y = relocal_pose["y"] + 0.5 * math.sin(relocal_pose["yaw"])

            if dev_only:
                with self._state_lock:
                    self._xf_origin_context = ""
                    self._nav_context = "wake_dev"
                self.send_nav_goal_tracked(wake_x, wake_y, relocal_pose["yaw"])
                return

            if not goal_room or goal_room not in ROOM_LOCATIONS:
                with self._state_lock:
                    self.state = "IDLE"
                    self._xf_origin_context = ""
                return

            with self._state_lock:
                origin = self._xf_origin_context
                if "legacy" in origin:
                    wake_ctx = "wake_then_legacy"
                elif goal_room == ENTRY_ROOM_ID and origin == "return_home":
                    wake_ctx = "wake_then_entry"
                else:
                    wake_ctx = "wake_then_room"
                self._nav_context = wake_ctx
                self._xf_origin_context = ""
            self.send_nav_goal_tracked(wake_x, wake_y, relocal_pose["yaw"])
        except Exception as e:
            self.get_logger().error(f"on_map_loaded: {e}")
            with self._state_lock:
                self.state = "IDLE"
                self._dev_floor_only = False

    def force_relocalize(self, pose_dict: dict) -> None:
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = pose_dict["x"]
        msg.pose.pose.position.y = pose_dict["y"]
        msg.pose.pose.orientation.z = math.sin(pose_dict["yaw"] / 2.0)
        msg.pose.pose.orientation.w = math.cos(pose_dict["yaw"] / 2.0)
        msg.pose.covariance[0] = 0.01
        msg.pose.covariance[7] = 0.01
        msg.pose.covariance[35] = 0.01
        for _ in range(5):
            self.initial_pose_pub.publish(msg)
            time.sleep(0.1)

    def send_nav_goal_tracked(self, x: float, y: float, yaw: float) -> None:
        if not self.nav_client.server_is_ready():
            self.get_logger().warn("Nav server not ready")
            self._on_nav_failed("nav2 not ready")
            return

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        goal_msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        send_future = self.nav_client.send_goal_async(goal_msg)
        send_future.add_done_callback(self._nav_goal_accepted_cb)

    def _nav_goal_accepted_cb(self, future) -> None:
        try:
            goal_handle = future.result()
        except Exception as e:
            self.get_logger().error(f"nav goal future error: {e}")
            self._on_nav_failed(str(e))
            return

        if not goal_handle.accepted:
            self.get_logger().warn("Nav goal rejected")
            self._on_nav_failed("goal rejected")
            return

        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._nav_result_cb)

    def _nav_result_cb(self, future) -> None:
        try:
            res_wrap = future.result()
            status = res_wrap.status
            ok = status == GoalStatus.STATUS_SUCCEEDED
        except Exception as e:
            self.get_logger().error(f"nav result error: {e}")
            self._on_nav_failed(str(e))
            return

        self._on_nav_done(ok)

    def _on_nav_failed(self, reason: str) -> None:
        self.get_logger().error(f"Navigation failed: {reason}")
        with self._state_lock:
            self.state = "IDLE"

        cur = self._tasks.get_current()
        if cur and cur.status in ("delivering", "returning_to_entry"):
            rid = cur.request_id
            self._tasks.fail_current()
            self._publish_task_status(rid, "failed", reason=reason)

        with self._state_lock:
            self.delivery_waiting = False
            self._dev_floor_only = False

        self._publish_queue_snapshot()

    def _on_nav_done(self, success: bool) -> None:
        if not success:
            self._on_nav_failed("nav aborted or failed")
            return

        ctx = ""
        with self._state_lock:
            ctx = self._nav_context

        if ctx in ("dev_elevator", "deliver_room_xf", "return_home_xf"):
            self.request_elevator()
            return

        if ctx == "wake_dev":
            with self._state_lock:
                self.state = "IDLE"
                self._dev_floor_only = False
                self.current_goal_room = None
                self.delivery_waiting = False
            self._publish({"msg_type": "dev_switch_floor_result", "ok": True, "current_floor": self.current_floor})
            return

        if ctx == "wake_then_room":
            with self._state_lock:
                gr = self.current_goal_room
            if gr and gr in ROOM_LOCATIONS:
                dest = ROOM_LOCATIONS[gr]
                with self._state_lock:
                    self.state = "NAVIGATING_TO_ROOM"
                    self._nav_context = "after_wake_room"
                self.send_nav_goal_tracked(dest["x"], dest["y"], dest["yaw"])
            return

        if ctx == "wake_then_entry":
            entry = ROOM_LOCATIONS[ENTRY_ROOM_ID]
            with self._state_lock:
                self.state = "NAVIGATING_TO_ROOM"
                self._nav_context = "after_wake_entry"
            self.send_nav_goal_tracked(entry["x"], entry["y"], entry["yaw"])
            return

        if ctx == "wake_then_legacy":
            with self._state_lock:
                gr = self.current_goal_room
            if gr and gr in ROOM_LOCATIONS:
                dest = ROOM_LOCATIONS[gr]
                with self._state_lock:
                    self.state = "NAVIGATING_TO_ROOM"
                    self._nav_context = "after_wake_legacy"
                self.send_nav_goal_tracked(dest["x"], dest["y"], dest["yaw"])
            return

        if ctx == "after_wake_room":
            cur = self._tasks.get_current()
            if cur:
                self._tasks.update_status(cur.request_id, "waiting_receipt")
                self._publish_task_status(cur.request_id, "waiting_receipt")
            with self._state_lock:
                self.state = "IDLE"
            return

        if ctx == "after_wake_entry":
            cur = self._tasks.complete_current()
            if cur:
                self._publish_task_status(cur.request_id, "completed")
            with self._state_lock:
                self.state = "IDLE"
                self.current_goal_room = ENTRY_ROOM_ID
                self.delivery_waiting = True
            self._publish_queue_snapshot()
            return

        if ctx == "after_wake_legacy":
            with self._state_lock:
                self.state = "IDLE"
            return

        if ctx == "deliver_room":
            cur = self._tasks.get_current()
            if cur:
                self._tasks.update_status(cur.request_id, "waiting_receipt")
                self._publish_task_status(cur.request_id, "waiting_receipt")
            with self._state_lock:
                self.state = "IDLE"
            return

        if ctx == "return_home":
            cur = self._tasks.complete_current()
            if cur:
                self._publish_task_status(cur.request_id, "completed")
            with self._state_lock:
                self.state = "IDLE"
                self.current_goal_room = ENTRY_ROOM_ID
                self.delivery_waiting = True
            self._publish_queue_snapshot()
            return

        if ctx == "legacy":
            with self._state_lock:
                self.state = "IDLE"
            return

        with self._state_lock:
            self.state = "IDLE"


def main(args=None):
    rclpy.init(args=args)
    node = SmartBuildingNavigator()
    from rclpy.executors import MultiThreadedExecutor

    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
