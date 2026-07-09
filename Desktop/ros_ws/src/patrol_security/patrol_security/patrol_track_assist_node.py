#!/usr/bin/env python3
"""P1c PATROL TRACK: visual align-then-chase + lost 360 scan (Nav2 fallback optional)."""
from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any, Optional

import rclpy
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

from patrol_security.mqtt_helper import PatrolMqttClient
from patrol_security.tracker_control import ChaseConfig, ChaseController


class PatrolTrackAssistNode(Node):
    def __init__(self) -> None:
        super().__init__("patrol_track_assist")
        self._active = False
        self._phase = "idle"
        self._track_mode = os.environ.get("PATROL_TRACK_MODE", "visual").strip().lower() or "visual"
        self._chase_config = ChaseConfig.from_env()
        self._chase = ChaseController()
        self._control_hz = float(os.environ.get("PATROL_TRACK_CONTROL_HZ", "15"))
        self._max_v = self._chase_config.max_linear_x
        self._goal_hz = float(os.environ.get("PATROL_TRACK_GOAL_HZ", "10"))
        self._scan_wz = float(os.environ.get("PATROL_SCAN_WZ", "0.25"))
        self._lost_sec = float(os.environ.get("PATROL_TRACK_LOST_SEC", "2.5"))
        self._obstacle_stop = os.environ.get("PATROL_TRACK_OBSTACLE_STOP", "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        self._obstacle_min_m = float(os.environ.get("PATROL_TRACK_OBSTACLE_MIN_M", "0.45"))
        self._obstacle_half_deg = float(os.environ.get("PATROL_TRACK_OBSTACLE_HALF_DEG", "20"))
        self._pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self._obs_lock = threading.Lock()
        self._last_person_mono = 0.0
        self._bearing_rad = 0.0
        self._target_bbox: tuple[int, int, int, int] | None = None
        self._frame_w = 640
        self._frame_h = 480
        self._target_center_x = 0.0
        self._last_control_state = ""
        self._last_chase_log_mono = 0.0
        self._resume_index: Optional[int] = None
        self._scan_ranges: list[float] = []
        self._nav = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self._scan_abort = threading.Event()
        self._stop = threading.Event()
        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self._on_pose, 10)
        self.create_subscription(String, "/patrol_security/person_detected", self._on_person, 10)
        if self._obstacle_stop:
            self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
        self._mqtt = PatrolMqttClient(on_request=self._on_mqtt_request)
        self._mqtt.start()
        self._thread = threading.Thread(target=self._track_loop, daemon=True, name="patrol_track_loop")
        self._thread.start()
        self.get_logger().info(
            f"patrol_track_assist mode={self._track_mode} control_hz={self._control_hz:.1f} "
            f"lost_sec={self._lost_sec:.2f} enter_dead={self._chase_config.dead_zone} "
            f"realign_dead={self._chase_config.align_dead_zone} "
            f"stop_y2={self._chase_config.stop_y2_ratio:.2f} "
            f"max_vx={self._chase_config.max_linear_x:.2f} obstacle_stop={self._obstacle_stop}"
        )

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        self._pose = {"x": float(p.x), "y": float(p.y), "yaw": float(yaw)}

    def _on_scan(self, msg: LaserScan) -> None:
        self._scan_ranges = list(msg.ranges)

    def _front_obstacle_clear(self) -> bool:
        if not self._obstacle_stop or not self._scan_ranges:
            return True
        half_rad = math.radians(self._obstacle_half_deg)
        n = len(self._scan_ranges)
        if n < 3:
            return True
        center = n // 2
        span = max(1, int(n * (half_rad / math.pi)))
        lo = max(0, center - span)
        hi = min(n, center + span + 1)
        for r in self._scan_ranges[lo:hi]:
            if not math.isfinite(r):
                continue
            if r < self._obstacle_min_m:
                return False
        return True

    def _on_person(self, msg: String) -> None:
        if not self._active:
            return
        try:
            data = json.loads(msg.data)
        except Exception:
            return
        visible = data.get("visible", True)
        bbox = data.get("bbox") or []
        if visible is False or not bbox or len(bbox) < 4:
            return
        bbox_t = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
        frame_w = int(data.get("frame_w", 0) or 0)
        frame_h = int(data.get("frame_h", 0) or 0)
        center_x = data.get("center_x")
        if center_x is None:
            center_x = (bbox_t[0] + bbox_t[2]) / 2.0
        with self._obs_lock:
            self._target_bbox = bbox_t
            self._target_center_x = float(center_x)
            if frame_w > 0:
                self._frame_w = frame_w
            if frame_h > 0:
                self._frame_h = frame_h
            self._bearing_rad = float(data.get("bearing_rad", 0.0))
            self._last_person_mono = time.monotonic()
        if self._phase in ("following", "scan_360"):
            self._phase = "following"
            self._scan_abort.set()

    def _on_mqtt_request(self, data: dict) -> None:
        mt = str(data.get("msg_type", "")).strip()
        if mt == "patrol_track_start":
            self._active = True
            self._phase = "following"
            self._resume_index = int(data.get("resume_index", 0))
            self._last_person_mono = time.monotonic()
            self._chase.reset()
            self._scan_abort.set()
            self._cancel_nav()
            self._publish_zero()
            self.get_logger().info(
                f"track start mode={self._track_mode} resume_index={self._resume_index}"
            )
        elif mt == "patrol_track_stop":
            self._stop_track(str(data.get("reason", "")))
        elif mt == "patrol_motion_mode":
            mode = str(data.get("mode", "") or "").strip().lower()
            if mode == "idle":
                self._stop_track("motion_idle")

    def _stop_track(self, reason: str) -> None:
        was_active = self._active or self._phase != "idle"
        self._active = False
        self._phase = "idle"
        self._scan_abort.set()
        self._chase.reset()
        with self._obs_lock:
            self._target_bbox = None
        self._publish_zero()
        self._cancel_nav()
        if was_active:
            # 视觉追人直连 /cmd_vel，多发几次零速防止与 control 环竞态
            for _ in range(3):
                self._publish_zero()
                time.sleep(0.02)
        self.get_logger().info(f"track stop reason={reason}")

    def _publish_zero(self) -> None:
        self._cmd_pub.publish(Twist())

    def _publish_twist(self, linear_x: float, angular_z: float) -> None:
        twist = Twist()
        twist.linear.x = float(linear_x)
        twist.angular.z = float(angular_z)
        self._cmd_pub.publish(twist)

    def _snapshot_obs(self) -> dict[str, Any]:
        with self._obs_lock:
            return {
                "bbox": self._target_bbox,
                "center_x": self._target_center_x,
                "frame_w": self._frame_w,
                "frame_h": self._frame_h,
                "last_person_mono": self._last_person_mono,
                "bearing_rad": self._bearing_rad,
            }

    def _track_loop(self) -> None:
        interval = 1.0 / max(1.0, self._control_hz if self._track_mode == "visual" else self._goal_hz)
        while not self._stop.is_set() and rclpy.ok():
            if not self._active:
                time.sleep(0.1)
                continue
            now = time.monotonic()
            if self._phase == "following":
                if now - self._last_person_mono > self._lost_sec:
                    self.get_logger().info(
                        f"track lost {now - self._last_person_mono:.2f}s > {self._lost_sec:.2f}s, scan 360"
                    )
                    self._phase = "scan_360"
                    self._publish_zero()
                    self._publish_status("scan_360", False)
                    self._run_scan_360()
                elif self._track_mode == "visual":
                    self._visual_chase_step()
                else:
                    self._send_follow_goal()
                    self._publish_status("following", True)
            time.sleep(interval)

    def _visual_chase_step(self) -> None:
        if not self._active:
            self._publish_zero()
            return
        obs = self._snapshot_obs()
        bbox = obs["bbox"]
        if bbox is None:
            self._publish_zero()
            self._publish_status("following", False)
            return

        obstacle_block = not self._front_obstacle_clear()
        out = self._chase.compute(
            bbox,
            float(obs["center_x"]),
            int(obs["frame_w"]),
            int(obs["frame_h"]),
            self._chase_config,
            lost=False,
            obstacle_block_forward=obstacle_block,
        )
        self._publish_twist(out.linear_x, out.angular_z)
        now = time.monotonic()
        if out.control_state != self._last_control_state or now - self._last_chase_log_mono > 2.0:
            self._last_chase_log_mono = now
            self.get_logger().info(
                f"visual_chase state={out.control_state} err={out.error_x:.3f} "
                f"y2={out.y2_ratio:.2f} vx={out.linear_x:.2f} wz={out.angular_z:.2f} "
                f"obstacle_block={obstacle_block}"
            )
        self._last_control_state = out.control_state
        self._publish_status("following", True, control_state=out.control_state)

    def _send_follow_goal(self) -> None:
        dist = min(2.0, max(0.8, self._max_v * 3))
        gx = self._pose["x"] + dist * math.cos(self._pose["yaw"] + self._bearing_rad)
        gy = self._pose["y"] + dist * math.sin(self._pose["yaw"] + self._bearing_rad)
        goal = PoseStamped()
        goal.header.frame_id = "map"
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.pose.position.x = gx
        goal.pose.position.y = gy
        yaw = self._pose["yaw"] + self._bearing_rad
        goal.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.orientation.w = math.cos(yaw / 2.0)
        if not self._nav.wait_for_server(timeout_sec=0.5):
            return
        msg = NavigateToPose.Goal()
        msg.pose = goal
        self._nav.send_goal_async(msg)

    def _cancel_nav(self) -> None:
        try:
            if self._nav.server_is_ready():
                self._nav.cancel_all_goals_async()
        except Exception:
            pass

    def _run_scan_360(self) -> None:
        twist = Twist()
        twist.angular.z = self._scan_wz
        duration = float(os.environ.get("PATROL_SCAN_SEC", "8"))
        self._scan_abort.clear()
        self._cancel_nav()
        t0 = time.monotonic()
        reacquired = False
        while self._active and (time.monotonic() - t0) < duration:
            if self._scan_abort.is_set():
                reacquired = self._phase == "following"
                break
            self._cmd_pub.publish(twist)
            time.sleep(0.1)
        self._publish_zero()
        if not self._active:
            return
        if reacquired or (time.monotonic() - self._last_person_mono) <= self._lost_sec:
            self._phase = "following"
            self._chase.reset()
            self.get_logger().info("scan 360: target reacquired, resume visual chase")
            self._publish_status("following", True)
            return
        self._phase = "lost_confirmed"
        self._active = False
        self._publish_zero()
        self.get_logger().info("scan 360: lost confirmed")
        self._publish_status("lost_confirmed", False)

    def _publish_status(self, phase: str, visible: bool, control_state: str = "") -> None:
        payload: dict[str, Any] = {
            "msg_type": "patrol_track_status",
            "phase": phase,
            "target_visible": visible,
            "last_bearing_rad": self._bearing_rad,
            "pose_x": self._pose["x"],
            "pose_y": self._pose["y"],
            "resume_index": self._resume_index,
            "track_mode": self._track_mode,
        }
        if control_state:
            payload["control_state"] = control_state
        self._mqtt.publish_status(payload)

    def destroy_node(self) -> bool:
        self._stop.set()
        self._stop_track("node_shutdown")
        self._mqtt.stop()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PatrolTrackAssistNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
