#!/usr/bin/env python3
"""P1c vehicle vision: GUARD manual rotate + view track, PATROL alerts, MJPEG stream."""
from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import rclpy
import requests
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from patrol_security.mjpeg_server import LiveFrameBuffer, frame_to_jpeg, start_mjpeg_server
from patrol_security.mqtt_helper import PatrolMqttClient
from patrol_security.person_selector import PersonDetection, detections_to_persons, select_nearest
from patrol_security.tracker_control import compute_angular_z, compute_error_x

PERSON_TOPIC = "/patrol_security/person_detected"


def _normalize_angle(rad: float) -> float:
    while rad > math.pi:
        rad -= 2.0 * math.pi
    while rad < -math.pi:
        rad += 2.0 * math.pi
    return rad


def _parse_bool(val, default: bool) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _resolve_model_path() -> str:
    for key in ("PATROL_YOLO_MODEL", "PATROL_VISION_MODEL"):
        v = os.environ.get(key, "").strip()
        if v:
            return os.path.expanduser(v)
    ros_ws = os.environ.get("AI_CAR_ROS_WS", os.path.expanduser("~/Desktop/rock_ws/ros_ws"))
    for name in ("yolo11n.pt", "yolo11n.rknn"):
        p = Path(ros_ws) / "person_detect_rknn" / name
        if p.is_file():
            return str(p)
    return str(Path(ros_ws) / "person_detect_rknn" / "yolo11n.pt")


def _camera_open_candidates() -> list:
    spec = os.environ.get("PATROL_VISION_CAMERA", "0").strip()
    out: list = []
    if spec.startswith("/dev/"):
        out.append(spec)
    elif spec.isdigit():
        out.append(int(spec))
        out.append(f"/dev/video{spec}")
    else:
        out.append(spec)
    extra = os.environ.get(
        "PATROL_VISION_CAMERA_FALLBACK",
        "/dev/video1,/dev/video2,/dev/video4,1,2",
    )
    for part in extra.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("/dev/"):
            if part not in out:
                out.append(part)
        elif part.isdigit():
            idx = int(part)
            for c in (idx, f"/dev/video{idx}"):
                if c not in out:
                    out.append(c)
    return out


def _open_v4l_camera(logger, candidates: list):
    for src in candidates:
        try:
            cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        except Exception:
            cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            logger.warn(f"camera open failed: {src!r}")
            continue
        ok, frame = cap.read()
        if ok and frame is not None:
            logger.info(f"camera opened: {src!r}")
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            return cap
        cap.release()
        logger.warn(f"camera opened but no frame: {src!r}")
    return None


class PersonDetector:
    def __init__(self, model_path: str, conf: float) -> None:
        self.conf = conf
        self._imgsz = int(os.environ.get("PATROL_YOLO_IMGSZ", "416"))
        self._yolo = None
        self._hog = None
        mp = Path(model_path).expanduser()
        candidates = [mp]
        if mp.suffix != ".pt":
            candidates.append(mp.with_suffix(".pt"))
        yolo_env = os.environ.get("PATROL_YOLO_MODEL", "").strip()
        if yolo_env:
            candidates.append(Path(yolo_env))
        for pt in candidates:
            if pt.is_file() and pt.suffix == ".pt":
                try:
                    from ultralytics import YOLO

                    self._yolo = YOLO(str(pt))
                    return
                except Exception:
                    continue
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        if self._yolo is not None:
            res = self._yolo.predict(frame, conf=self.conf, verbose=False, imgsz=self._imgsz)
            if not res or res[0].boxes is None:
                return []
            out = []
            for box in res[0].boxes:
                if int(box.cls[0]) != 0:
                    continue
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                out.append((int(x1), int(y1), int(x2), int(y2), float(box.conf[0])))
            return out
        if self._hog is not None:
            rects, _ = self._hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
            return [(int(x), int(y), int(x + w), int(y + h), 0.5) for x, y, w, h in rects]
        return []


class PatrolVisionNode(Node):
    GUARD_IDLE = "idle"
    GUARD_MANUAL = "manual_rotating"
    GUARD_VIEW = "view_tracking"

    def __init__(self) -> None:
        super().__init__("patrol_vision_node")
        self._camera_spec = os.environ.get("PATROL_VISION_CAMERA", "0").strip()
        self._camera_candidates = _camera_open_candidates()
        self._conf = float(os.environ.get("PATROL_VISION_CONF", "0.30"))
        self._guard_view_track_enabled = True
        self._patrol_track_enabled = True
        self._window = int(os.environ.get("PATROL_VISION_WINDOW", "30"))
        self._threshold = int(os.environ.get("PATROL_VISION_THRESHOLD", "6"))
        self._snapshot_url = os.environ.get(
            "PATROL_SNAPSHOT_URL", "http://127.0.0.1:8000/api/security/snapshot"
        ).strip()
        self._kp = float(os.environ.get("PATROL_GUARD_KP", "0.55"))
        self._dead_zone = float(os.environ.get("PATROL_GUARD_DEAD_ZONE", "0.15"))
        self._max_angular_z = float(os.environ.get("PATROL_GUARD_MAX_ANGULAR_Z", "0.35"))
        self._invert_angular = os.environ.get("PATROL_GUARD_INVERT_ANGULAR", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self._manual_kp = float(os.environ.get("PATROL_GUARD_MANUAL_KP", "0.8"))
        self._manual_tol_deg = float(os.environ.get("PATROL_GUARD_MANUAL_TOLERANCE_DEG", "3"))
        self._manual_timeout_sec = float(os.environ.get("PATROL_GUARD_MANUAL_TIMEOUT_SEC", "20"))
        self._lost_timeout = float(os.environ.get("PATROL_GUARD_LOST_TIMEOUT", "0.5"))
        self._control_rate = float(os.environ.get("PATROL_GUARD_CONTROL_HZ", "15"))
        model = _resolve_model_path()
        self._detector = PersonDetector(model, self._conf)
        self._security_active = False
        self._sub_state = ""
        self._patrol_epoch = 0
        self._track_local_active = False
        self._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self._floor = "1F"
        self._guard_phase = self.GUARD_IDLE
        self._manual_target_yaw: Optional[float] = None
        self._manual_delta_deg: Optional[float] = None
        self._manual_start_mono = 0.0
        self._control_lock = threading.Lock()
        self._latest_target: Optional[PersonDetection] = None
        self._frame_w = 640
        self._frame_h = 480
        self._last_target_mono = 0.0
        self._hits: deque[bool] = deque(maxlen=max(5, self._window))
        self._last_event_mono = 0.0
        self._last_person_skip_log_mono = 0.0
        self._live_buffer = LiveFrameBuffer()
        self._stream_port = int(os.environ.get("PATROL_CAMERA_STREAM_PORT", "8089"))
        self._stream_host = os.environ.get("PATROL_CAMERA_STREAM_HOST", "0.0.0.0").strip() or "0.0.0.0"
        start_mjpeg_server(
            self._live_buffer,
            host=self._stream_host,
            port=self._stream_port,
            logger=lambda m: self.get_logger().info(m),
        )
        cmd_topic = os.environ.get("PATROL_GUARD_CMD_VEL_TOPIC", "/patrol_security/guard_cmd_vel").strip()
        cmd_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._cmd_pub = self.create_publisher(Twist, cmd_topic, cmd_qos)
        self.get_logger().info(f"guard/manual rotation publishes to {cmd_topic} (switcher forwards in guard mode)")
        self._cmd_wz = 0.0
        self._cmd_drive_active = False
        self._last_guard_log_mono = 0.0
        self._person_pub = self.create_publisher(String, PERSON_TOPIC, 10)
        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self._on_pose, 10)
        self._mqtt = PatrolMqttClient(on_request=self._on_mqtt_request)
        self._mqtt.start()
        self._publish_stream_url()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="patrol_vision_loop")
        self._thread.start()
        control_period = 1.0 / max(1.0, self._control_rate)
        self._control_timer = self.create_timer(control_period, self._control_tick)
        self.get_logger().info(
            f"patrol_vision camera={self._camera_spec} model={model} "
            f"snapshot={self._snapshot_url} stream_port={self._stream_port} "
            f"control_hz={self._control_rate:.1f} dead_zone={self._dead_zone}"
        )

    def _publish_stream_url(self) -> None:
        advertised = os.environ.get("PATROL_CAMERA_STREAM_URL", "").strip()
        if advertised:
            url = advertised
        else:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
            except Exception:
                ip = "127.0.0.1"
            url = f"http://{ip}:{self._stream_port}/stream"
        self._mqtt.publish_status({"msg_type": "patrol_camera_stream", "stream_url": url})
        self.get_logger().info(f"camera stream URL: {url}")

    def _draw_dets(
        self,
        frame: np.ndarray,
        dets: list,
        target: Optional[PersonDetection] = None,
        *,
        dead_zone: float,
        draw_overlay: bool = True,
    ) -> np.ndarray:
        vis = frame.copy()
        if not draw_overlay:
            return vis
        h, w = vis.shape[:2]
        center_x = w // 2
        dead_half = int((dead_zone * w) / 2.0)
        cv2.line(vis, (center_x, 0), (center_x, h), (0, 255, 255), 1)
        cv2.rectangle(
            vis,
            (center_x - dead_half, 0),
            (center_x + dead_half, h),
            (255, 255, 0),
            1,
        )
        for x1, y1, x2, y2, c in dets:
            is_target = target is not None and (x1, y1, x2, y2) == target.bbox
            color = (0, 255, 0) if is_target else (120, 120, 120)
            thickness = 2 if is_target else 1
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, thickness)
            label = f"person {c:.2f}" + (" [TARGET]" if is_target else "")
            cv2.putText(vis, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
        return vis

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        self._pose = {"x": float(p.x), "y": float(p.y), "yaw": float(yaw)}

    def _on_mqtt_request(self, data: dict) -> None:
        mt = str(data.get("msg_type", "")).strip()
        if mt == "guard_rotate":
            try:
                delta_deg = float(data.get("delta_deg", 0))
            except (TypeError, ValueError):
                return
            self._start_manual_rotate(delta_deg)
        elif mt == "guard_rotate_cancel":
            self._cancel_manual_rotate()
        elif mt == "patrol_track_start":
            self._track_local_active = True
            self.get_logger().info("track local active (patrol_track_start)")
        elif mt == "patrol_track_stop":
            reason = str(data.get("reason", "") or "")
            self._force_stop_patrol_motion(reason=f"patrol_track_stop:{reason}")
        elif mt == "patrol_motion_mode":
            mode = str(data.get("mode", "") or "").strip().lower()
            if mode == "idle":
                self._force_stop_patrol_motion(reason="patrol_motion_mode:idle")
        elif mt == "patrol_vision_config":
            self._apply_vision_config(data)

    def _apply_vision_config(self, data: dict) -> None:
        if "conf" in data:
            try:
                conf = float(data.get("conf", self._conf))
            except (TypeError, ValueError):
                self.get_logger().warn("patrol_vision_config: invalid conf")
            else:
                conf = max(0.05, min(0.95, conf))
                self._conf = conf
                self._detector.conf = conf
                self.get_logger().info(f"vision conf updated -> {conf:.2f}")
        if "guard_view_track_enabled" in data:
            enabled = _parse_bool(data.get("guard_view_track_enabled"), self._guard_view_track_enabled)
            self._set_guard_view_track_enabled(enabled)
        if "patrol_track_enabled" in data:
            enabled = _parse_bool(data.get("patrol_track_enabled"), self._patrol_track_enabled)
            if enabled != self._patrol_track_enabled:
                self._patrol_track_enabled = enabled
                self.get_logger().info(f"patrol_track_enabled -> {enabled}")

    def _sync_vision_from_master(self, m: dict) -> None:
        if "guard_view_track_enabled" in m:
            enabled = _parse_bool(m.get("guard_view_track_enabled"), self._guard_view_track_enabled)
            if enabled != self._guard_view_track_enabled:
                self._set_guard_view_track_enabled(enabled)
        if "patrol_track_enabled" in m:
            enabled = _parse_bool(m.get("patrol_track_enabled"), self._patrol_track_enabled)
            if enabled != self._patrol_track_enabled:
                self._patrol_track_enabled = enabled
                self.get_logger().info(f"patrol_track_enabled (master) -> {enabled}")
        if "vision_conf" in m:
            try:
                conf = max(0.05, min(0.95, float(m.get("vision_conf", self._conf))))
            except (TypeError, ValueError):
                return
            if abs(conf - self._conf) > 1e-6:
                self._conf = conf
                self._detector.conf = conf
                self.get_logger().info(f"vision conf (master) -> {conf:.2f}")

    def _mode_vision_enabled(self) -> bool:
        """当前子状态下是否启用视角/追人相关能力（叠框、告警、截图）。"""
        if not self._security_active:
            return False
        sub = self._sub_state
        if sub in ("guard", "guard_timer", "guard_view_track"):
            return self._guard_view_track_enabled
        if sub in ("patrol", "track") or self._track_local_active:
            return self._patrol_track_enabled
        return False

    def _draw_overlay_enabled(self) -> bool:
        return self._mode_vision_enabled()

    def _render_frame(
        self,
        frame: np.ndarray,
        dets: list,
        target: Optional[PersonDetection],
    ) -> np.ndarray:
        return self._draw_dets(
            frame,
            dets,
            target,
            dead_zone=self._dead_zone,
            draw_overlay=self._draw_overlay_enabled(),
        )

    def _set_guard_view_track_enabled(self, enabled: bool) -> None:
        prev = self._guard_view_track_enabled
        self._guard_view_track_enabled = enabled
        if prev and not enabled and self._guard_phase == self.GUARD_VIEW:
            self._guard_phase = self.GUARD_IDLE
            with self._control_lock:
                self._latest_target = None
            self._publish_zero()
            self._publish_guard_status()
            self.get_logger().info("guard view_track disabled by config")
        self.get_logger().info(f"guard_view_track_enabled -> {enabled}")

    def _force_stop_patrol_motion(self, *, reason: str = "") -> None:
        """退出巡逻 / TRACK 结束时：停 GUARD 角速度，避免 switcher 持续 hold 自转。"""
        self._track_local_active = False
        prev_phase = self._guard_phase
        self._guard_phase = self.GUARD_IDLE
        self._manual_target_yaw = None
        self._manual_delta_deg = None
        self._manual_start_mono = 0.0
        with self._control_lock:
            self._latest_target = None
        self._publish_zero()
        if prev_phase != self.GUARD_IDLE:
            self._publish_guard_status()
        if reason:
            self.get_logger().info(f"patrol vision motion halt: {reason}")

    def _in_guard_mode(self) -> bool:
        return self._security_active and self._sub_state in (
            "guard",
            "guard_timer",
            "guard_view_track",
        )

    def _in_track_mode(self) -> bool:
        return self._security_active and (self._track_local_active or self._sub_state == "track")

    def _sync_master(self) -> None:
        m = self._mqtt.master_snapshot
        prev_active = self._security_active
        self._security_active = bool(m.get("security_active", False))
        self._sub_state = str(m.get("patrol_sub_state", "") or "").strip().lower()
        self._patrol_epoch = int(m.get("patrol_epoch", 0) or 0)
        self._sync_vision_from_master(m)
        if prev_active and not self._security_active:
            self._force_stop_patrol_motion(reason="master:security_inactive")

    def _start_manual_rotate(self, delta_deg: float) -> None:
        if not self._in_guard_mode():
            self.get_logger().warn("guard_rotate ignored: not in guard mode")
            return
        if self._guard_phase == self.GUARD_VIEW:
            self.get_logger().warn("guard_rotate ignored: view tracking active")
            return
        if abs(delta_deg) > 180:
            self.get_logger().warn(f"guard_rotate rejected: |delta|={delta_deg}")
            return
        self._manual_delta_deg = delta_deg
        self._manual_target_yaw = _normalize_angle(self._pose["yaw"] - math.radians(delta_deg))
        self._manual_start_mono = time.monotonic()
        self._guard_phase = self.GUARD_MANUAL
        self._publish_guard_status()
        self.get_logger().info(f"guard manual rotate delta_deg={delta_deg}")

    def _cancel_manual_rotate(self) -> None:
        self._manual_target_yaw = None
        self._manual_delta_deg = None
        self._manual_start_mono = 0.0
        if self._guard_phase == self.GUARD_MANUAL:
            self._finish_manual_rotate(reason="cancelled")

    def _finish_manual_rotate(self, *, reason: str = "") -> None:
        self._guard_phase = self.GUARD_IDLE
        self._manual_target_yaw = None
        self._manual_delta_deg = None
        self._manual_start_mono = 0.0
        self._publish_zero()
        self._publish_guard_status()
        if reason:
            self.get_logger().info(f"guard manual rotate done: {reason}")

    def _manual_angular_wz(self, err_rad: float) -> float:
        wz = self._manual_kp * err_rad
        if self._invert_angular:
            wz = -wz
        return max(-self._max_angular_z, min(self._max_angular_z, wz))

    def _publish_guard_status(self) -> None:
        self._mqtt.publish_status(
            {
                "msg_type": "guard_status",
                "guard_phase": self._guard_phase,
                "delta_deg": self._manual_delta_deg,
                "patrol_epoch": self._patrol_epoch,
            }
        )

    def _publish_zero(self) -> None:
        self._cmd_wz = 0.0
        self._cmd_drive_active = False
        self._cmd_pub.publish(Twist())

    def _publish_angular(self, wz: float) -> None:
        wz = float(wz)
        if self._cmd_drive_active and abs(wz - self._cmd_wz) < 0.008:
            return
        self._cmd_wz = wz
        self._cmd_drive_active = True
        twist = Twist()
        twist.angular.z = self._cmd_wz
        self._cmd_pub.publish(twist)

    def _guard_cmd_allowed(self) -> bool:
        if not self._security_active:
            return False
        if self._track_local_active or self._sub_state == "track":
            return False
        if self._guard_phase in (self.GUARD_VIEW, self.GUARD_MANUAL):
            return self._sub_state not in ("patrol", "track")
        return self._in_guard_mode() and self._sub_state not in ("patrol", "track")

    def _control_tick(self) -> None:
        with self._control_lock:
            target = self._latest_target
            frame_w = self._frame_w
            frame_h = self._frame_h
        self._guard_control(target, frame_w)
        if self._in_track_mode():
            self._publish_track_observation(target, frame_w, frame_h)

    def _guard_control(self, target: Optional[PersonDetection], frame_w: int) -> None:
        if not self._security_active:
            self._publish_zero()
            return
        if not self._guard_cmd_allowed():
            self._publish_zero()
            return

        now = time.monotonic()
        if target is not None and self._guard_view_track_enabled:
            self._last_target_mono = now
            if self._guard_phase in (self.GUARD_IDLE, self.GUARD_MANUAL):
                self._guard_phase = self.GUARD_VIEW
                self._manual_target_yaw = None
                self._manual_delta_deg = None
                self._manual_start_mono = 0.0
                self._publish_guard_status()
                self.get_logger().info("guard view_track: person detected, centering bbox")
        elif target is not None:
            self._last_target_mono = now

        if self._guard_phase == self.GUARD_VIEW:
            if not self._guard_view_track_enabled:
                self._guard_phase = self.GUARD_IDLE
                self._publish_zero()
                self._publish_guard_status()
                return
            if target is None and (now - self._last_target_mono) > self._lost_timeout:
                self._guard_phase = self.GUARD_IDLE
                with self._control_lock:
                    self._latest_target = None
                self._publish_zero()
                self._publish_guard_status()
                self.get_logger().info("guard view_track: target lost, back to idle")
                return
            if target is not None:
                err = compute_error_x(target.center_x, frame_w)
                wz = compute_angular_z(
                    err,
                    self._kp,
                    self._dead_zone,
                    self._max_angular_z,
                    invert=self._invert_angular,
                )
                self._publish_angular(wz)
                if now - self._last_guard_log_mono > 2.0:
                    self._last_guard_log_mono = now
                    self.get_logger().info(
                        f"guard view_track err={err:.3f} wz={wz:.3f} "
                        f"sub_state={self._sub_state!r} security={self._security_active}"
                    )
            else:
                self._publish_zero()
            return

        if self._guard_phase == self.GUARD_MANUAL and self._manual_target_yaw is not None:
            err = _normalize_angle(self._manual_target_yaw - self._pose["yaw"])
            err_deg = abs(math.degrees(err))
            if err_deg <= self._manual_tol_deg:
                self._finish_manual_rotate(reason=f"reached target (err={err_deg:.1f}°)")
                return
            elapsed = time.monotonic() - self._manual_start_mono
            if self._manual_start_mono > 0 and elapsed > self._manual_timeout_sec:
                self._finish_manual_rotate(
                    reason=f"timeout after {elapsed:.1f}s (err={err_deg:.1f}°, check /amcl_pose)"
                )
                return
            self._publish_angular(self._manual_angular_wz(err))
            if elapsed > 1.0 and now - self._last_guard_log_mono > 2.0:
                self._last_guard_log_mono = now
                self.get_logger().info(
                    f"guard manual rotate err={err_deg:.1f}° wz={self._cmd_wz:.3f} "
                    f"pose_yaw={math.degrees(self._pose['yaw']):.1f}° "
                    f"target_yaw={math.degrees(self._manual_target_yaw):.1f}° "
                    f"invert={self._invert_angular}"
                )
            return

        self._publish_zero()

    def _loop(self) -> None:
        cap = _open_v4l_camera(self.get_logger(), self._camera_candidates)
        if cap is None:
            self.get_logger().error(
                "cannot open UVC camera; try: ls /dev/video* ; "
                "export PATROL_VISION_CAMERA=/dev/video2"
            )
            return
        cooldown = float(os.environ.get("PATROL_PERSON_COOLDOWN_SEC", "2"))
        loop_sleep = float(os.environ.get("PATROL_VISION_LOOP_SEC", "0.04"))
        jpeg_quality = int(os.environ.get("PATROL_JPEG_QUALITY", "62"))
        jpeg_width = int(os.environ.get("PATROL_JPEG_MAX_WIDTH", "640"))
        while not self._stop.is_set() and rclpy.ok():
            self._sync_master()
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            dets = self._detector.detect(frame)
            persons = detections_to_persons(dets)
            target = select_nearest(persons)
            h, w = frame.shape[:2]
            now = time.monotonic()
            with self._control_lock:
                self._latest_target = target
                self._frame_w = w
                self._frame_h = h
                if target is not None:
                    self._last_target_mono = now
            vis = self._render_frame(frame, dets, target)
            jpg = frame_to_jpeg(vis, max_width=jpeg_width, quality=jpeg_quality)
            if jpg:
                self._live_buffer.set_jpeg(jpg)
            has_person = len(dets) > 0
            self._hits.append(has_person)
            in_track = self._in_track_mode()
            if has_person and not self._security_active:
                if time.monotonic() - self._last_person_skip_log_mono > 15.0:
                    self._last_person_skip_log_mono = time.monotonic()
                    self.get_logger().warn(
                        "person detected but security_active=false — "
                        "check PC 已进入巡逻且 MQTT master/status 可达"
                    )
            if (
                self._security_active
                and has_person
                and self._mode_vision_enabled()
                and sum(1 for x in self._hits if x) >= self._threshold
                and time.monotonic() - self._last_event_mono > cooldown
            ):
                conf = float(target.score) if target else (float(dets[0][4]) if dets else 0.0)
                if self._sub_state in ("guard", "guard_timer", "guard_view_track"):
                    hint = "guard"
                elif in_track:
                    hint = "track"
                else:
                    hint = "patrol"
                self._upload_snapshot_async(vis, dets, conf, hint)
                self._emit_mqtt_person_event(vis, dets, target, hint)
                if in_track:
                    self.get_logger().info(
                        f"person alert (track mode, conf={conf:.2f}) — snapshot+MQTT"
                    )
                self._last_event_mono = time.monotonic()
            time.sleep(loop_sleep)
        cap.release()

    def _publish_track_observation(
        self, target: Optional[PersonDetection], frame_w: int, frame_h: int = 0
    ) -> None:
        """TRACK 期间高频发布 bbox/方位，供 patrol_track_assist 视觉追人。"""
        if target is None:
            payload = {
                "visible": False,
                "bbox": [],
                "bearing_rad": 0.0,
                "confidence": 0.0,
                "frame_w": frame_w,
                "frame_h": frame_h,
            }
        else:
            bbox = list(target.bbox)
            payload = {
                "visible": True,
                "bbox": bbox,
                "center_x": float(target.center_x),
                "confidence": float(target.score),
                "bearing_rad": self._bbox_bearing(bbox, frame_w),
                "frame_w": frame_w,
                "frame_h": frame_h,
            }
        ros_msg = String()
        ros_msg.data = json.dumps(payload)
        self._person_pub.publish(ros_msg)

    def _emit_mqtt_person_event(
        self,
        frame: np.ndarray,
        dets: list,
        target: Optional[PersonDetection],
        hint: str,
    ) -> None:
        """MQTT 识人事件（触发 PC 进 TRACK 等）；截图由 _upload_snapshot_async 单独上传。"""
        best = target
        if best is None and dets:
            best_det = max(dets, key=lambda d: d[4])
            best = PersonDetection(score=best_det[4], bbox=best_det[:4])
        bbox = list(best.bbox) if best else []
        conf = float(best.score) if best else 0.0
        payload = {
            "msg_type": "security_person_event",
            "patrol_epoch": self._patrol_epoch,
            "sub_state_hint": hint,
            "confidence": conf,
            "bbox": bbox,
            "pose_x": self._pose["x"],
            "pose_y": self._pose["y"],
            "pose_yaw": self._pose["yaw"],
            "floor": self._floor,
        }
        self._mqtt.publish_status(payload)
        bearing = self._bbox_bearing(bbox, frame.shape[1])
        ros_msg = String()
        ros_msg.data = json.dumps({"bbox": bbox, "confidence": conf, "bearing_rad": bearing})
        self._person_pub.publish(ros_msg)

    def _emit_person_event(
        self,
        frame: np.ndarray,
        dets: list,
        target: Optional[PersonDetection],
    ) -> None:
        hint = "guard" if self._sub_state in ("guard", "guard_timer", "guard_view_track") else "patrol"
        self._emit_mqtt_person_event(frame, dets, target, hint)
        conf = float(target.score) if target else (float(dets[0][4]) if dets else 0.0)
        self._upload_snapshot_async(frame, dets, conf, hint)

    @staticmethod
    def _bbox_bearing(bbox: list, frame_w: int) -> float:
        if len(bbox) < 4 or frame_w <= 0:
            return 0.0
        cx = (bbox[0] + bbox[2]) / 2.0
        return ((cx / frame_w) - 0.5) * math.radians(60)

    def _upload_snapshot_async(self, frame: np.ndarray, dets: list, conf: float, hint: str) -> None:
        vis = frame.copy()
        for x1, y1, x2, y2, _c in dets:
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 2)
        h, w = vis.shape[:2]
        if w > 640:
            vis = cv2.resize(vis, (640, int(h * 640 / w)))
        ok, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ok:
            return
        jpeg_bytes = buf.tobytes()
        meta = {
            "patrol_epoch": self._patrol_epoch,
            "hint": hint,
            "floor": self._floor,
            "confidence": conf,
            "pose": dict(self._pose),
            "bbox_json": json.dumps(dets[0][:4] if dets else []),
        }
        threading.Thread(
            target=self._upload_snapshot_worker,
            args=(jpeg_bytes, meta),
            daemon=True,
            name="patrol_snapshot_upload",
        ).start()

    def _upload_snapshot_worker(self, jpeg_bytes: bytes, meta: dict) -> None:
        files = {"file": ("alert.jpg", jpeg_bytes, "image/jpeg")}
        pose = meta.get("pose") or {}
        data = {
            "robot_id": os.environ.get("MQTT_ROBOT_ID", "robot01"),
            "patrol_epoch": str(meta.get("patrol_epoch", 0)),
            "sub_state_hint": str(meta.get("hint", "")),
            "floor": str(meta.get("floor", "")),
            "confidence": str(meta.get("confidence", 0)),
            "pose_x": str(pose.get("x", 0)),
            "pose_y": str(pose.get("y", 0)),
            "pose_yaw": str(pose.get("yaw", 0)),
            "bbox": str(meta.get("bbox_json", "[]")),
        }
        headers = {}
        key = os.environ.get("PATROL_UPLOAD_KEY", "").strip()
        if key:
            headers["X-Patrol-Upload-Key"] = key
        try:
            resp = requests.post(self._snapshot_url, files=files, data=data, headers=headers, timeout=8)
            if resp.status_code >= 400:
                self.get_logger().warn(
                    f"snapshot upload HTTP {resp.status_code}: {resp.text[:200]}"
                )
            else:
                self.get_logger().info(f"snapshot uploaded ok ({len(jpeg_bytes)} bytes)")
        except Exception as e:
            self.get_logger().warn(f"snapshot upload failed: {e}")

    def destroy_node(self) -> bool:
        self._stop.set()
        self._track_local_active = False
        self._publish_zero()
        self._mqtt.stop()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PatrolVisionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
