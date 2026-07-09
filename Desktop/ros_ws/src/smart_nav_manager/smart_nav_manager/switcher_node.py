from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

import paho.mqtt.client as mqtt
import rclpy
from action_msgs.msg import GoalStatus
from rcl_interfaces.msg import ParameterDescriptor
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav2_msgs.srv import ClearEntireCostmap, LoadMap
from paho.mqtt.enums import CallbackAPIVersion
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy


def _bridge_ready_paths() -> list[str]:
    paths: list[str] = []
    env_path = os.environ.get("SMART_NAV_BRIDGE_READY_FILE", "").strip()
    if env_path:
        paths.append(env_path)
    paths.append("/tmp/smart_nav_bridge.ready")
    ros_ws = os.environ.get("AI_CAR_ROS_WS", "").strip()
    if ros_ws:
        paths.append(os.path.join(ros_ws, ".nav_bridge_ready"))
    home = os.path.expanduser("~")
    paths.append(os.path.join(home, ".nav_bridge.ready"))
    out: list[str] = []
    for p in paths:
        if p and p not in out:
            out.append(p)
    return out


def _write_bridge_ready_marker(logger: Any = None) -> None:
    stamp = str(time.time())
    for path in _bridge_ready_paths():
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="ascii") as f:
                f.write(stamp + "\n")
                f.flush()
                os.fsync(f.fileno())
            if logger is not None:
                logger.info(f"nav_action_bridge ready file: {path}")
        except OSError as e:
            if logger is not None:
                logger.warn(f"Could not write bridge ready file {path}: {e}")


# ================= ⚠️ 全局配置区域 (my_map6 1F + my_map8 2F) =================

MAP_BASE_DIR = "/home/rock/Desktop/rock_ws/ros_ws/install/rt_robot_nav2/share/rt_robot_nav2/map/"

FLOOR_MAPS = {
    "1F": "my_map6.yaml",
    "2F": "my_map8.yaml",
}

ELEVATOR_ENABLED = True

# initial_pose：出电梯后重定位点；entry：本层去电梯导航点（1F=105 · 2F=200）
ELEVATOR_POSITIONS = {
    "1F": {
        "entry": {"x": 5.475, "y": 3.725, "yaw": 0.5215},
        "initial_pose": {"x": 5.475, "y": 3.725, "yaw": 0.5215},
    },
    "2F": {
        "entry": {"x": 0.04172, "y": 0.00707, "yaw": -0.70409},
        "initial_pose": {"x": 0.04172, "y": 0.00707, "yaw": -0.70409},
    },
}

# 房间号与坐标（1F my_map6 · 2F my_map8 · 2026-07）
ROOM_LOCATIONS = {
    "100": {"floor": "1F", "x": -0.05135, "y": 0.5785, "yaw": 0.52154},
    "101": {"floor": "1F", "x": 0.782, "y": 4.39, "yaw": 0.00554},
    "102": {"floor": "1F", "x": 3.46, "y": 0.261, "yaw": 0.00393},
    "103": {"floor": "1F", "x": 3.22, "y": 6.06, "yaw": 0.00406},
    "104": {"floor": "1F", "x": 5.97, "y": 1.86, "yaw": 0.00254},
    "105": {"floor": "1F", "x": 5.475, "y": 3.725, "yaw": 0.5215},
    "200": {"floor": "2F", "x": 0.04172, "y": 0.00707, "yaw": -0.70409},
    "201": {"floor": "2F", "x": -1.97, "y": 1.34, "yaw": 0.00178},
    "202": {"floor": "2F", "x": 3.05, "y": -1.77, "yaw": 0.00196},
    "203": {"floor": "2F", "x": 1.36, "y": 2.81, "yaw": 0.00144},
    "204": {"floor": "2F", "x": -0.264, "y": 3.67, "yaw": 0.00168},
}

# 100=1F 出入口/起点；105=1F 电梯；200=2F 起点/电梯，不作为配送点
NON_DELIVERY_ROOM_IDS = ("100", "105", "200")
DELIVERY_ROOM_IDS = tuple(k for k in ROOM_LOCATIONS if k not in NON_DELIVERY_ROOM_IDS)
ENTRY_ROOM_ID = "100"

MQTT_BROKER = "broker.emqx.io"
TOPIC_ELEV_REQ = "elevator/request"
TOPIC_ELEV_RESP = "elevator/response"

# 兼容旧调试：仍订阅 raw 房间指令（可选）
TOPIC_NAV_ROOM_LEGACY = "robot/nav_room"
TOPIC_NAV_CANCEL = "robot/nav_cancel"


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


class NavActionBridge(Node):
    """
    独立节点 + 专用 spin 线程发送 /navigate_to_pose。
    ros2 action info 在 CLI 里可用，但主节点 executor 与 MQTT/语音争抢时 ActionClient 无法完成 DDS 发现。
    """

    def __init__(
        self,
        on_failed: Callable[[str], None],
        on_done: Callable[[bool, int], None],
    ) -> None:
        super().__init__("nav_action_bridge")
        self._on_failed = on_failed
        self._on_done = on_done
        self._client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._cancel = self.create_client(CancelGoal, "/navigate_to_pose/_action/cancel_goal")
        self._lock = threading.Lock()
        self._pending: Optional[Tuple[float, float, float]] = None
        self._deadline: Optional[float] = None
        self._pending_epoch = 0
        self._inflight_epoch = 0
        self._sending = False
        self._connected = False
        self._fail_reported = False
        self._timer = self.create_timer(0.1, self._tick)

    def enqueue(self, x: float, y: float, yaw: float, deadline: float, nav_epoch: int) -> None:
        with self._lock:
            self._pending = (x, y, yaw)
            self._deadline = deadline
            self._pending_epoch = int(nav_epoch)
            self._fail_reported = False

    def cancel(self) -> None:
        with self._lock:
            self._pending = None
            self._deadline = None
            self._fail_reported = False
        if self._cancel.service_is_ready():
            self._cancel.call_async(CancelGoal.Request())

    def _mark_connected(self) -> None:
        if not self._connected:
            self._connected = True
            self.get_logger().info("Connected to /navigate_to_pose (nav_action_bridge)")
            _write_bridge_ready_marker(self.get_logger())

    def _tick(self) -> None:
        if self._sending:
            return

        with self._lock:
            pending = self._pending
            deadline = self._deadline

        if pending is None:
            if self._client.server_is_ready():
                self._mark_connected()
            return

        if not self._client.server_is_ready():
            self._client.wait_for_server(timeout_sec=0.2)
        if not self._client.server_is_ready():
            if deadline is not None and time.monotonic() > deadline:
                with self._lock:
                    if self._pending is pending and not self._fail_reported:
                        self._pending = None
                        self._deadline = None
                        self._fail_reported = True
                        wait_sec = float(os.environ.get("SMART_NAV_ACTION_WAIT_SEC", "30"))
                        self.get_logger().warn(
                            f"Nav server not ready after {wait_sec:.0f}s wait on /navigate_to_pose"
                        )
                        self._on_failed("nav2 not ready")
            return

        self._mark_connected()
        x, y, yaw = pending
        with self._lock:
            if self._pending is not pending:
                return
            self._pending = None
            self._deadline = None
            self._sending = True

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        goal_msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(yaw / 2.0)

        send_future = self._client.send_goal_async(goal_msg)
        send_future.add_done_callback(self._goal_sent_cb)

    def _goal_sent_cb(self, future) -> None:
        self._sending = False
        try:
            goal_handle = future.result()
        except Exception as e:
            self.get_logger().error(f"nav goal future error: {e}")
            self._on_failed(str(e))
            return

        if not goal_handle.accepted:
            self.get_logger().warn("Nav goal rejected")
            self._on_failed("goal rejected")
            return

        with self._lock:
            self._inflight_epoch = self._pending_epoch
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._result_cb)

    def _result_cb(self, future) -> None:
        try:
            res_wrap = future.result()
            ok = res_wrap.status == GoalStatus.STATUS_SUCCEEDED
        except Exception as e:
            self.get_logger().error(f"nav result error: {e}")
            self._on_failed(str(e))
            return
        with self._lock:
            epoch = self._inflight_epoch
        self._on_done(ok, epoch)


class SmartBuildingNavigator(Node):
    """
    统一 Android 端 MQTT（robot/{id}/request|status）与 Nav2/电梯逻辑。
    流程：默认在 100 待命 → 用户 pickup → 送货员尾号+确认送货 → 导航房间 → 用户 confirm_receipt → 回 100。
    """

    def __init__(self) -> None:
        super().__init__("smart_building_navigator")

        self.declare_parameter("robot_id", "robot01")
        self.declare_parameter(
            "elevator_pose_fallback",
            False,
            ParameterDescriptor(
                description=(
                    "若 true：靠近电梯入口 1m 内也可用位姿触发 MQTT 呼叫；"
                    "手动推车/定位漂移时建议 false，仅导航到电梯成功后再发呼叫。"
                )
            ),
        )
        rid = str(self.get_parameter("robot_id").get_parameter_value().string_value or "robot01").strip() or "robot01"
        self._elevator_pose_fallback = bool(
            self.get_parameter("elevator_pose_fallback").get_parameter_value().bool_value
        )
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
        self._active_tour_id: Optional[str] = None
        self._nav_epoch = 0
        self._patrol_pending: Optional[dict[str, Any]] = None
        self._patrol_spin_abort = threading.Event()
        self._patrol_motion_mode = "idle"

        # CONNACK 由 loop 线程处理
        self._mqtt_connected = threading.Event()

        self.mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION1)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
        self.mqtt_client.on_message = self.on_mqtt_message
        try:
            self.mqtt_client.connect(MQTT_BROKER, 1883, 60)
            self.mqtt_client.loop_start()
        except Exception as e:
            self.get_logger().error(f"MQTT Error: {e}")

        self.load_map_client = self.create_client(LoadMap, "/map_server/load_map")
        self._nav_bridge: Optional[NavActionBridge] = None
        self.clear_global_costmap_client = self.create_client(ClearEntireCostmap, "/global_costmap/clear_entire_costmap")
        self.clear_local_costmap_client = self.create_client(ClearEntireCostmap, "/local_costmap/clear_entire_costmap")

        self.pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self.pose_callback, 10
        )
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self._cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self._last_guard_twist = Twist()
        _guard_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            Twist,
            "/patrol_security/guard_cmd_vel",
            self._on_guard_cmd_vel,
            _guard_qos,
        )
        # 仅 hold 非零角速度，防 Nav2 偶发零速覆盖；新指令在 callback 里立即转发
        self.create_timer(0.04, self._hold_guard_cmd_vel)
        self.current_pos = None

        self.status_timer = threading.Thread(target=self.publish_status_loop, daemon=True)
        self.status_timer.start()

        self.get_logger().info(
            f"Initialized floor={self.current_floor}; MQTT status publishes to {self._topic_status}, "
            f"commands on {self._topic_request}, nav_room on {TOPIC_NAV_ROOM_LEGACY}, "
            f"nav_cancel on {TOPIC_NAV_CANCEL}; nav goals via nav_action_bridge"
        )

    def attach_nav_bridge(self, bridge: NavActionBridge) -> None:
        self._nav_bridge = bridge

    # --- MQTT ---
    @staticmethod
    def _connack_ok(rc) -> bool:
        """兼容 MQTT3 的 int 与 MQTT5 的 ReasonCode。"""
        if rc is None:
            return True
        if hasattr(rc, "is_failure"):
            return not bool(rc.is_failure)
        try:
            return int(rc) == 0
        except (TypeError, ValueError):
            return True

    def on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        if not self._connack_ok(rc):
            self.get_logger().error(f"MQTT connect failed rc={rc!r}, will not subscribe")
            return
        # QoS1：略提高指令送达率（须在订阅建立后再发 nav_room，见 on_connect 后立即发 heartbeat）
        client.subscribe(self._topic_request, 1)
        client.subscribe(TOPIC_ELEV_RESP, 1)
        client.subscribe(TOPIC_NAV_ROOM_LEGACY, 1)
        client.subscribe(TOPIC_NAV_CANCEL, 1)
        self._mqtt_connected.set()
        self.get_logger().info(
            f"MQTT session up: publish status -> {self._topic_status}; "
            f"subscribed request={self._topic_request}, {TOPIC_ELEV_RESP}, "
            f"{TOPIC_NAV_ROOM_LEGACY}, {TOPIC_NAV_CANCEL}"
        )
        try:
            self._publish_robot_heartbeat()
            self._publish_queue_snapshot()
        except Exception as e:
            self.get_logger().error(f"MQTT first status publish error: {e}")

    def on_mqtt_disconnect(self, client, userdata, rc, properties=None):
        self._mqtt_connected.clear()
        self.get_logger().warn(f"MQTT disconnected (rc={rc!r}); status/nav input paused until reconnect")

    def on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        payload_str = msg.payload.decode("utf-8", errors="replace")
        try:
            if topic == TOPIC_ELEV_RESP:
                self.handle_elevator_response(payload_str)
                return
            if topic == TOPIC_NAV_ROOM_LEGACY:
                self.handle_legacy_nav_room(payload_str)
                return
            if topic == TOPIC_NAV_CANCEL:
                self.handle_nav_cancel(payload_str)
                return
            data = json.loads(payload_str)
            self.handle_app_request(data)
        except json.JSONDecodeError:
            self.get_logger().warn(f"Bad JSON on {topic}")
        except Exception as e:
            self.get_logger().error(f"MQTT handle error: {e}")

    def _publish(self, obj: dict) -> None:
        try:
            inf = self.mqtt_client.publish(self._topic_status, json.dumps(obj, ensure_ascii=False))
            prc = getattr(inf, "rc", None)
            if prc is not None and prc != mqtt.MQTT_ERR_SUCCESS:
                self.get_logger().warn(f"MQTT publish rc={prc} topic={self._topic_status}")
        except Exception as e:
            self.get_logger().error(f"MQTT publish error: {e}")

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

    def _current_map_yaml(self) -> str:
        return str(FLOOR_MAPS.get(self.current_floor, "") or "")

    def _publish_robot_heartbeat(self) -> None:
        with self._state_lock:
            st = self.state
            df = self.delivery_waiting
            fl = self.current_floor
            gr = self.current_goal_room
            epoch = self._nav_epoch
            pos = self.current_pos
        pose_x = pose_y = pose_yaw = None
        if pos is not None:
            pose_x = float(pos.position.x)
            pose_y = float(pos.position.y)
            qz = float(pos.orientation.z)
            qw = float(pos.orientation.w)
            pose_yaw = math.atan2(2.0 * qw * qz, 1.0 - 2.0 * qz * qz)
        self._publish(
            {
                "msg_type": "robot_heartbeat",
                "nav_state": st,
                "current_floor": fl,
                "current_goal_room": gr,
                "nav_epoch": epoch,
                "delivery_waiting": df,
                "elevator_pose_fallback": self._elevator_pose_fallback,
                "current_map_yaml": self._current_map_yaml(),
                "pose_x": pose_x,
                "pose_y": pose_y,
                "pose_yaw": pose_yaw,
            }
        )

    def publish_status_loop(self):
        wait_log_ts = 0.0
        while rclpy.ok():
            if not self._mqtt_connected.wait(timeout=0.5):
                now = time.monotonic()
                if now - wait_log_ts >= 8.0:
                    self.get_logger().warn(
                        "MQTT 仍未收到 CONNACK（未连上 broker），status 不会发出；"
                        "请检查网络与 broker，连上后才会订阅 nav_room"
                    )
                    wait_log_ts = now
                continue
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
        elif mt == "tour_nav":
            self._handle_tour_nav(data)
        elif mt in ("tour_cancel", "tour_stop_in_place"):
            self._handle_tour_stop_in_place(data)
        elif mt == "tour_return_home":
            self._handle_tour_return_home(data)
        elif mt == "patrol_nav_waypoint":
            self._handle_patrol_nav_waypoint(data)
        elif mt == "patrol_motion_mode":
            self._handle_patrol_motion_mode(data)
        elif mt == "patrol_track_start":
            self._handle_patrol_track_start(data)
        elif mt == "patrol_track_stop":
            self._handle_patrol_track_stop(data)
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
                    "request_id": str(msg.get("request_id", "")).strip(),
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
                    "request_id": str(msg.get("request_id", "")).strip(),
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

        if not ELEVATOR_ENABLED:
            self._publish(
                {
                    "msg_type": "dev_switch_floor_result",
                    "ok": False,
                    "reason": "电梯已禁用",
                }
            )
            return

        with self._state_lock:
            self._dev_floor_only = True
            self.target_next_floor = target
            self.delivery_waiting = False

        self.get_logger().info(f"dev_switch_floor: {cf} -> {target}")
        self._go_to_elevator(nav_context="dev_elevator")

    def _publish_tour_arrived(self, nav_epoch: int) -> None:
        with self._state_lock:
            tid = self._active_tour_id
            room = self.current_goal_room
        if not room:
            return
        payload = {
            "msg_type": "tour_arrived",
            "room": str(room),
            "nav_epoch": int(nav_epoch),
        }
        if tid:
            payload["tour_id"] = tid
        self._publish(payload)
        with self._state_lock:
            self._active_tour_id = None

    def _allocate_nav_epoch(self) -> int:
        with self._state_lock:
            self._nav_epoch += 1
            return self._nav_epoch

    def _handle_tour_nav(self, msg: dict) -> None:
        tour_id = str(msg.get("tour_id", "")).strip()
        room = str(msg.get("room", "")).strip()
        if not tour_id or not room:
            self._publish(
                {
                    "msg_type": "tour_nav_result",
                    "ok": False,
                    "tour_id": tour_id,
                    "reason": "缺少 tour_id / room",
                }
            )
            return
        if room not in DELIVERY_ROOM_IDS:
            self._publish(
                {
                    "msg_type": "tour_nav_result",
                    "ok": False,
                    "tour_id": tour_id,
                    "reason": f"房间须为: {', '.join(DELIVERY_ROOM_IDS)}",
                }
            )
            return
        cur = self._tasks.get_current()
        if cur and cur.status in ("delivering", "waiting_dropoff", "returning_to_entry"):
            self._publish(
                {
                    "msg_type": "tour_nav_result",
                    "ok": False,
                    "tour_id": tour_id,
                    "reason": "送货任务进行中，无法开导览",
                }
            )
            return
        with self._state_lock:
            st = self.state
        if st != "IDLE":
            self._publish(
                {
                    "msg_type": "tour_nav_result",
                    "ok": False,
                    "tour_id": tour_id,
                    "reason": f"小车忙({st})，仅 IDLE 可导览",
                }
            )
            return
        with self._state_lock:
            self._active_tour_id = tour_id
            self.delivery_waiting = False
        self._publish({"msg_type": "tour_nav_result", "ok": True, "tour_id": tour_id, "room": room})
        self.get_logger().info(f"tour_nav -> {room} ({tour_id})")
        self._begin_navigate_to_room(room, nav_context="tour")

    def _handle_tour_stop_in_place(self, msg: dict) -> None:
        """导览途中截停：取消 Nav2 目标，原地待机（不返航）。"""
        tour_id = str(msg.get("tour_id", "")).strip()
        with self._state_lock:
            active = self._active_tour_id
        if tour_id and active and tour_id != active:
            self._publish(
                {
                    "msg_type": "tour_stop_in_place_result",
                    "ok": False,
                    "tour_id": tour_id,
                    "reason": "tour_id 与当前导览不一致",
                }
            )
            return

        self.cancel_nav()
        with self._state_lock:
            self.state = "IDLE"
            self.current_goal_room = None
            self.target_next_floor = None
            self._nav_context = ""
            self._xf_origin_context = ""
            if tour_id:
                self._active_tour_id = tour_id
            elif active:
                self._active_tour_id = active
            self.delivery_waiting = False

        tid = tour_id or active or ""
        self._publish(
            {
                "msg_type": "tour_stop_in_place_result",
                "ok": True,
                "tour_id": tid,
            }
        )
        self.get_logger().info(f"tour_stop_in_place tour_id={tid}")

    def _handle_tour_cancel(self, msg: dict) -> None:
        """兼容旧 tour_cancel：等同原地截停（不再返航）。"""
        self._handle_tour_stop_in_place(msg)

    def _publish_tour_return_complete(self) -> None:
        with self._state_lock:
            tid = self._active_tour_id
        if not tid:
            return
        self._publish(
            {
                "msg_type": "tour_return_complete",
                "ok": True,
                "tour_id": tid,
                "room": ENTRY_ROOM_ID,
            }
        )
        with self._state_lock:
            self._active_tour_id = None

    def _handle_tour_return_home(self, msg: dict) -> None:
        tour_id = str(msg.get("tour_id", "")).strip()
        with self._state_lock:
            active = self._active_tour_id
        if tour_id and active and tour_id != active:
            self._publish(
                {
                    "msg_type": "tour_return_result",
                    "ok": False,
                    "tour_id": tour_id,
                    "reason": "tour_id 与当前导览不一致",
                }
            )
            return
        with self._state_lock:
            st = self.state
        if st not in ("IDLE", "NAVIGATING_TO_ROOM"):
            self.cancel_nav()
        with self._state_lock:
            if tour_id:
                self._active_tour_id = tour_id
            self.delivery_waiting = False
        self._publish(
            {
                "msg_type": "tour_return_result",
                "ok": True,
                "tour_id": tour_id or active or "",
            }
        )
        self.get_logger().info(f"tour_return_home -> {ENTRY_ROOM_ID} ({tour_id})")
        self._begin_navigate_to_room(ENTRY_ROOM_ID, nav_context="tour_return")

    def handle_nav_cancel(self, payload_str: str = "") -> None:
        """语音/外部截停：取消 Nav2 目标并回到 IDLE（不清任务队列）。"""
        reason = "voice"
        raw = (payload_str or "").strip()
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                reason = str(data.get("reason", reason) or reason)
            except json.JSONDecodeError:
                pass
        elif raw:
            reason = raw

        self.cancel_nav()
        with self._state_lock:
            ctx = self._nav_context
            self.state = "IDLE"
            self.current_goal_room = None
            self.target_next_floor = None
            self._nav_context = ""
            self._xf_origin_context = ""
            if not ctx.startswith("tour"):
                pass
            elif reason in ("abort", "clear", "hard_cancel"):
                self._active_tour_id = None

        self.get_logger().info(f"nav_cancel reason={reason!r}")
        self._publish(
            {
                "msg_type": "nav_cancel_result",
                "ok": True,
                "reason": reason,
            }
        )

    def handle_legacy_nav_room(self, payload_str: str) -> None:
        with self._state_lock:
            if self.state in ("WAITING_ELEVATOR", "SWITCHING_MAP"):
                self.get_logger().warn("nav_room ignored: waiting for elevator / map switch")
                return
        try:
            raw = payload_str.strip()
            if raw.startswith("{"):
                data = json.loads(raw)
                room_id = str(data.get("room", "")).strip()
            else:
                room_id = raw
        except Exception as e:
            self.get_logger().warn(f"nav_room parse error: {e}")
            return
        if not room_id:
            self.get_logger().warn("nav_room empty payload")
            return
        if room_id not in ROOM_LOCATIONS:
            self.get_logger().warn(
                f"nav_room unknown room {room_id!r}; known: {sorted(ROOM_LOCATIONS.keys())}"
            )
            return
        self.get_logger().info(f"nav_room -> navigate {room_id}")
        self._begin_navigate_to_room(room_id, nav_context="legacy")

    def _abort_patrol_spin(self, *, clear_pending: bool = False) -> None:
        self._patrol_spin_abort.set()
        stop = Twist()
        self._cmd_vel_pub.publish(stop)
        if clear_pending:
            with self._state_lock:
                self._patrol_pending = None

    def _clear_guard_drive(self) -> None:
        self._last_guard_twist = Twist()
        self._cmd_vel_pub.publish(Twist())

    def _on_guard_cmd_vel(self, msg: Twist) -> None:
        with self._state_lock:
            mode = self._patrol_motion_mode
        if mode not in ("guard_idle", "guard_view_track"):
            return
        self._last_guard_twist = msg
        self._cmd_vel_pub.publish(msg)

    def _hold_guard_cmd_vel(self) -> None:
        """Hold 非零角速度（不重复处理新指令，仅抗 Nav2 零速覆盖）。"""
        with self._state_lock:
            mode = self._patrol_motion_mode
            twist = self._last_guard_twist
        if mode not in ("guard_idle", "guard_view_track"):
            return
        if abs(twist.angular.z) < 0.001 and abs(twist.linear.x) < 0.001:
            return
        self._cmd_vel_pub.publish(twist)

    def _handle_patrol_motion_mode(self, data: dict) -> None:
        mode = str(data.get("mode", "idle") or "idle").strip().lower()
        with self._state_lock:
            prev = self._patrol_motion_mode
            self._patrol_motion_mode = mode
        self.get_logger().info(f"patrol_motion_mode -> {mode}")
        if prev in ("guard_idle", "guard_view_track") and mode not in (
            "guard_idle",
            "guard_view_track",
        ):
            self._clear_guard_drive()
            self.get_logger().info("guard drive released (cleared guard_cmd_vel forward)")
        if mode in ("track_nav", "idle", "nav"):
            self._abort_patrol_spin(clear_pending=(mode == "idle"))
            if mode in ("track_nav", "idle"):
                self.cancel_nav(invalidate=False)
            if mode == "idle":
                self._clear_guard_drive()
                stop = Twist()
                for _ in range(3):
                    self._cmd_vel_pub.publish(stop)
        elif mode in ("guard_idle", "guard_view_track"):
            # 驻守/视角跟踪：取消 Nav2，避免 controller 与 patrol_vision 争用 /cmd_vel
            self._abort_patrol_spin(clear_pending=False)
            self.cancel_nav(invalidate=False)
            self._clear_guard_drive()
            self.get_logger().info("guard mode: Nav2 canceled; patrol_vision drives rotation")

    def _handle_patrol_track_start(self, data: dict) -> None:
        self.get_logger().info(f"patrol_track_start resume={data.get('resume_label')}")
        self._abort_patrol_spin()
        self.cancel_nav(invalidate=False)
        with self._state_lock:
            self._patrol_motion_mode = "track_nav"
            self.state = "IDLE"

    def _handle_patrol_track_stop(self, data: dict) -> None:
        reason = str(data.get("reason", "")).strip().lower()
        self.get_logger().info(f"patrol_track_stop reason={reason}")
        self._abort_patrol_spin(clear_pending=True)
        self._clear_guard_drive()
        stop = Twist()
        for _ in range(3):
            self._cmd_vel_pub.publish(stop)
        with self._state_lock:
            if reason == "exit_security":
                self._patrol_motion_mode = "idle"
            elif self._patrol_motion_mode == "track_nav":
                self._patrol_motion_mode = "nav"
            elif reason in ("motion_idle", "resume_patrol"):
                pass

    def _handle_patrol_nav_waypoint(self, data: dict) -> None:
        req = str(data.get("request_id", "")).strip()
        if not req:
            self._publish_patrol_waypoint_failed(req, "missing request_id")
            return
        try:
            x = float(data["x"])
            y = float(data["y"])
            yaw = float(data.get("yaw", 0.0))
        except (KeyError, TypeError, ValueError):
            self._publish_patrol_waypoint_failed(req, "invalid x/y/yaw")
            return
        floor = str(data.get("floor", self.current_floor) or self.current_floor)
        action = str(data.get("action", "nav_only") or "nav_only")
        pending = {
            "request_id": req,
            "index": data.get("index"),
            "label": data.get("label", ""),
            "x": x,
            "y": y,
            "yaw": yaw,
            "action": action,
            "floor": floor,
            "patrol_epoch": data.get("patrol_epoch"),
            "map_yaml": data.get("map_yaml", ""),
        }
        with self._state_lock:
            if self.state in ("WAITING_ELEVATOR", "SWITCHING_MAP"):
                self._publish_patrol_waypoint_failed(req, "elevator/map switch busy")
                return
            self._patrol_pending = pending
        if floor == self.current_floor:
            self._begin_patrol_nav_same_floor(pending)
        else:
            with self._state_lock:
                self.target_next_floor = floor
                self._xf_origin_context = "patrol_xf"
            self._go_to_elevator(nav_context="patrol_xf")

    def _begin_patrol_nav_same_floor(self, pending: dict) -> None:
        self.cancel_nav(invalidate=False)
        nav_epoch = self._allocate_nav_epoch()
        with self._state_lock:
            self.state = "NAVIGATING_TO_ROOM"
            self._nav_context = "patrol"
            self._patrol_pending = pending
        self.send_nav_goal_tracked(
            float(pending["x"]),
            float(pending["y"]),
            float(pending["yaw"]),
            nav_epoch=nav_epoch,
        )

    def _publish_patrol_waypoint_done(self, pending: Optional[dict]) -> None:
        if not pending:
            return
        self._publish(
            {
                "msg_type": "patrol_waypoint_done",
                "ok": True,
                "request_id": pending.get("request_id", ""),
                "index": pending.get("index"),
                "label": pending.get("label", ""),
                "patrol_epoch": pending.get("patrol_epoch"),
            }
        )
        with self._state_lock:
            if self._patrol_pending and self._patrol_pending.get("request_id") == pending.get("request_id"):
                self._patrol_pending = None
            self.state = "IDLE"

    def _publish_patrol_waypoint_failed(self, request_id: str, reason: str) -> None:
        self._publish(
            {
                "msg_type": "patrol_waypoint_failed",
                "ok": False,
                "request_id": request_id,
                "reason": reason,
            }
        )

    def _finish_patrol_waypoint(self, *, success: bool, reason: str = "") -> None:
        with self._state_lock:
            pending = dict(self._patrol_pending) if self._patrol_pending else None
        if not pending:
            return
        if success:
            self._publish_patrol_waypoint_done(pending)
        else:
            self._publish_patrol_waypoint_failed(str(pending.get("request_id", "")), reason or "failed")

    def _run_patrol_spin_360(self) -> None:
        pending_snapshot: Optional[dict] = None
        with self._state_lock:
            pending_snapshot = dict(self._patrol_pending) if self._patrol_pending else None
        self._patrol_spin_abort.clear()

        def _spin() -> None:
            twist = Twist()
            twist.angular.z = float(os.environ.get("PATROL_SPIN_WZ", "0.25"))
            duration = float(os.environ.get("PATROL_SPIN_SEC", str(2 * math.pi / 0.25)))
            stop = Twist()
            t0 = time.monotonic()
            while time.monotonic() - t0 < duration:
                if self._patrol_spin_abort.is_set():
                    self._cmd_vel_pub.publish(stop)
                    self.get_logger().info("patrol spin_360 aborted (track/motion)")
                    return
                self._cmd_vel_pub.publish(twist)
                time.sleep(0.1)
            self._cmd_vel_pub.publish(stop)
            if self._patrol_spin_abort.is_set():
                return
            self._finish_patrol_waypoint(success=True)

        threading.Thread(target=_spin, daemon=True, name="patrol_spin_360").start()

    # --- 导航 ---
    def cancel_nav(self, *, invalidate: bool = True) -> None:
        if self._nav_bridge is not None:
            self._nav_bridge.cancel()
        if invalidate:
            with self._state_lock:
                self._nav_epoch += 1
                self.get_logger().info(
                    f"nav cancelled; nav_epoch -> {self._nav_epoch} (invalidate in-flight goals)"
                )

    def _begin_navigate_to_room(self, room_id: str, nav_context: str) -> None:
        if room_id not in ROOM_LOCATIONS:
            self.get_logger().error(f"Unknown room {room_id}")
            return

        with self._state_lock:
            if self.state in ("WAITING_ELEVATOR", "SWITCHING_MAP"):
                self.get_logger().warn(
                    f"Navigate to {room_id} skipped: waiting for elevator arrival / map switch"
                )
                return

        target_info = ROOM_LOCATIONS[room_id]
        target_floor = target_info["floor"]

        self.cancel_nav(invalidate=False)
        nav_epoch = self._allocate_nav_epoch()

        with self._state_lock:
            self.current_goal_room = room_id
            self._nav_context = nav_context

        if target_floor == self.current_floor:
            self.get_logger().info(f"Navigate same floor -> {room_id} (nav_epoch={nav_epoch})")
            with self._state_lock:
                self.state = "NAVIGATING_TO_ROOM"
                self._nav_context = nav_context
            self.send_nav_goal_tracked(
                target_info["x"], target_info["y"], target_info["yaw"], nav_epoch=nav_epoch
            )
            return

        if not ELEVATOR_ENABLED:
            self.get_logger().warn(f"Cross-floor nav to {room_id} aborted (elevator disabled)")
            with self._state_lock:
                self.state = "IDLE"
                self.current_goal_room = None
            return

        self.get_logger().info(f"Cross floor -> elevator then {room_id}")
        with self._state_lock:
            self.target_next_floor = target_floor
            self._xf_origin_context = nav_context
        self._go_to_elevator(nav_context=nav_context + "_xf")

    def _go_to_elevator(self, nav_context: str) -> None:
        if not ELEVATOR_ENABLED:
            self.get_logger().warn("Elevator disabled; _go_to_elevator ignored")
            with self._state_lock:
                self.state = "IDLE"
                self.target_next_floor = None
                self._dev_floor_only = False
            return
        with self._state_lock:
            self.state = "GOING_TO_ELEVATOR"
            self._nav_context = nav_context
        elev = ELEVATOR_POSITIONS[self.current_floor]["entry"]
        self.send_nav_goal_tracked(elev["x"], elev["y"], elev["yaw"])

    def pose_callback(self, msg) -> None:
        self.current_pos = msg.pose.pose
        if not ELEVATOR_ENABLED:
            return
        with self._state_lock:
            st = self.state
        if not self._elevator_pose_fallback:
            return
        if st != "GOING_TO_ELEVATOR" or not self.current_pos:
            return
        elev_entry = ELEVATOR_POSITIONS[self.current_floor]["entry"]
        dist = math.sqrt(
            (self.current_pos.position.x - elev_entry["x"]) ** 2
            + (self.current_pos.position.y - elev_entry["y"]) ** 2
        )
        if dist < 1.0:
            self.get_logger().info("Pose near elevator (fallback)")
            self.request_elevator()

    def request_elevator(self) -> None:
        if not ELEVATOR_ENABLED:
            return
        with self._state_lock:
            if self.state != "GOING_TO_ELEVATOR":
                return
            self.state = "WAITING_ELEVATOR"
            tgt = self.target_next_floor
        self.cancel_nav()
        req_payload = {"current_floor": self.current_floor, "target_floor": tgt, "action": "call"}
        self.mqtt_client.publish(TOPIC_ELEV_REQ, json.dumps(req_payload))
        self.get_logger().info(
            "Elevator MQTT call sent; navigation paused until elevator response (arrived)"
        )

    def handle_elevator_response(self, payload_str: str) -> None:
        if not ELEVATOR_ENABLED:
            return
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
        if not ELEVATOR_ENABLED:
            self.get_logger().warn("Elevator disabled; switch_floor_map ignored")
            return
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
        if not ELEVATOR_ENABLED:
            return
        try:
            resp = future.result()
            if resp.result != LoadMap.Response().RESULT_SUCCESS:
                self.get_logger().error("Map load failed")
                with self._state_lock:
                    self.state = "IDLE"
                    self._dev_floor_only = False
                return

            self.get_logger().info(f"Map {new_floor} loaded ({FLOOR_MAPS.get(new_floor)})")
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
                if "patrol" in origin:
                    wake_ctx = "wake_then_patrol"
                elif "tour_return" in origin:
                    wake_ctx = "wake_then_tour_return"
                elif "tour" in origin:
                    wake_ctx = "wake_then_tour"
                elif "legacy" in origin:
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

    def send_nav_goal_tracked(
        self, x: float, y: float, yaw: float, *, nav_epoch: Optional[int] = None
    ) -> None:
        """任意线程可调用；由 nav_action_bridge 专用 spin 线程发送 goal。"""
        if self._nav_bridge is None:
            self.get_logger().error("nav_action_bridge not attached")
            self._on_nav_failed("nav bridge not ready")
            return
        if nav_epoch is None:
            nav_epoch = self._allocate_nav_epoch()
        wait_sec = float(os.environ.get("SMART_NAV_ACTION_WAIT_SEC", "30"))
        self._nav_bridge.enqueue(x, y, yaw, time.monotonic() + wait_sec, int(nav_epoch))

    def _on_nav_failed(self, reason: str) -> None:
        with self._state_lock:
            if self.state in ("WAITING_ELEVATOR", "SWITCHING_MAP"):
                self.get_logger().info(
                    f"Navigation failure ignored during elevator transfer: {reason}"
                )
                return
        self.get_logger().error(f"Navigation failed: {reason}")
        with self._state_lock:
            ctx = self._nav_context
        if ctx in ("patrol", "after_wake_patrol", "wake_then_patrol"):
            self._finish_patrol_waypoint(success=False, reason=reason)
            return
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

    def _on_nav_done(self, success: bool, nav_epoch: int = 0) -> None:
        with self._state_lock:
            current_epoch = self._nav_epoch
        if nav_epoch != current_epoch:
            self.get_logger().info(
                f"nav_done ignored stale epoch={nav_epoch} current={current_epoch}"
            )
            return

        if not success:
            with self._state_lock:
                st = self.state
            if st in ("WAITING_ELEVATOR", "SWITCHING_MAP"):
                self.get_logger().info(
                    "Nav ended while paused for elevator/map switch; not treating as failure"
                )
                return
            self._on_nav_failed("nav aborted or failed")
            return

        ctx = ""
        with self._state_lock:
            ctx = self._nav_context

        # legacy_xf / tour_xf：跨层先到电梯再 MQTT 呼梯（电梯已禁用）
        if ctx in (
            "dev_elevator",
            "deliver_room_xf",
            "return_home_xf",
            "legacy_xf",
            "tour_xf",
            "tour_return_xf",
            "patrol_xf",
        ):
            if ELEVATOR_ENABLED:
                self.request_elevator()
            else:
                self.get_logger().warn(f"Nav ctx {ctx!r} needs elevator; aborted")
                with self._state_lock:
                    self.state = "IDLE"
                    self._dev_floor_only = False
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

        if ctx == "wake_then_tour":
            with self._state_lock:
                gr = self.current_goal_room
            if gr and gr in ROOM_LOCATIONS:
                dest = ROOM_LOCATIONS[gr]
                with self._state_lock:
                    self.state = "NAVIGATING_TO_ROOM"
                    self._nav_context = "after_wake_tour"
                self.send_nav_goal_tracked(dest["x"], dest["y"], dest["yaw"])
            return

        if ctx == "wake_then_tour_return":
            entry = ROOM_LOCATIONS[ENTRY_ROOM_ID]
            with self._state_lock:
                self.state = "NAVIGATING_TO_ROOM"
                self._nav_context = "after_wake_tour_return"
            self.send_nav_goal_tracked(entry["x"], entry["y"], entry["yaw"])
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

        if ctx == "after_wake_tour":
            self._publish_tour_arrived(nav_epoch)
            with self._state_lock:
                self.state = "IDLE"
            return

        if ctx == "after_wake_tour_return":
            self._publish_tour_return_complete()
            with self._state_lock:
                self.state = "IDLE"
                self.current_goal_room = ENTRY_ROOM_ID
                self.delivery_waiting = True
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
            self._publish_tour_arrived(nav_epoch)
            with self._state_lock:
                self.state = "IDLE"
            return

        if ctx == "patrol":
            with self._state_lock:
                pending = dict(self._patrol_pending) if self._patrol_pending else None
            if pending and str(pending.get("action", "")) == "spin_360":
                with self._state_lock:
                    self.state = "IDLE"
                self._run_patrol_spin_360()
                return
            self._finish_patrol_waypoint(success=True)
            return

        if ctx == "after_wake_patrol":
            with self._state_lock:
                pending = dict(self._patrol_pending) if self._patrol_pending else None
            if pending:
                with self._state_lock:
                    self.state = "NAVIGATING_TO_ROOM"
                    self._nav_context = "patrol"
                self.send_nav_goal_tracked(
                    float(pending["x"]),
                    float(pending["y"]),
                    float(pending["yaw"]),
                )
            return

        if ctx == "tour":
            self._publish_tour_arrived(nav_epoch)
            with self._state_lock:
                self.state = "IDLE"
            return

        if ctx == "tour_return":
            self._publish_tour_return_complete()
            with self._state_lock:
                self.state = "IDLE"
                self.current_goal_room = ENTRY_ROOM_ID
                self.delivery_waiting = True
            return

        with self._state_lock:
            self.state = "IDLE"


def main(args=None):
    rclpy.init(args=args)
    node = SmartBuildingNavigator()
    bridge = NavActionBridge(on_failed=node._on_nav_failed, on_done=node._on_nav_done)
    node.attach_nav_bridge(bridge)

    from rclpy.executors import MultiThreadedExecutor, SingleThreadedExecutor

    bridge_executor = SingleThreadedExecutor()
    bridge_executor.add_node(bridge)
    bridge_thread = threading.Thread(target=bridge_executor.spin, daemon=True, name="nav_action_bridge_spin")
    bridge_thread.start()

    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        bridge_executor.shutdown()
        bridge.destroy_node()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

