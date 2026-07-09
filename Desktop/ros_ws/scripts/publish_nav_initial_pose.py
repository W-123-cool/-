#!/usr/bin/python3
"""Publish /initialpose from map yaml with retries until AMCL responds (ROS Foxy)."""
from __future__ import annotations

import math
import os
import sys
import time
from typing import Optional

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node

try:
    import yaml
except ImportError:
    yaml = None


def load_pose_from_yaml(path: str) -> Optional[dict]:
    if not path or not os.path.isfile(path):
        return None
    if yaml is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        return None
    pose = data.get("rt_robot_initial_pose") or data.get("initial_pose")
    if not isinstance(pose, dict):
        return None
    try:
        return {
            "x": float(pose.get("x", 0.0)),
            "y": float(pose.get("y", 0.0)),
            "yaw": float(pose.get("yaw", 0.0)),
        }
    except (TypeError, ValueError):
        return None


def default_pose() -> dict:
    return {
        "x": float(os.environ.get("VOICE_NAV_INIT_X", "-0.254")),
        "y": float(os.environ.get("VOICE_NAV_INIT_Y", "0.551")),
        "yaw": float(os.environ.get("VOICE_NAV_INIT_YAW", "0.203")),
    }


class InitialPosePublisher(Node):
    def __init__(self, pose: dict, max_sec: float, interval_sec: float) -> None:
        super().__init__("publish_nav_initial_pose")
        self._pose = pose
        self._pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self._got_amcl = False
        self._deadline = time.monotonic() + max_sec
        self._interval = interval_sec
        self._next_publish = time.monotonic()
        self.create_subscription(
            PoseWithCovarianceStamped,
            "/amcl_pose",
            self._on_amcl,
            10,
        )
        self.create_timer(0.5, self._tick)
        self.get_logger().info(
            f"initial pose retry: x={pose['x']:.3f} y={pose['y']:.3f} yaw={pose['yaw']:.3f}"
        )

    def _on_amcl(self, msg: PoseWithCovarianceStamped) -> None:
        if not self._got_amcl:
            self._got_amcl = True
            self.get_logger().info(
                f"AMCL pose ok: x={msg.pose.pose.position.x:.3f} "
                f"y={msg.pose.pose.position.y:.3f}"
            )

    def _make_msg(self) -> PoseWithCovarianceStamped:
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = self._pose["x"]
        msg.pose.pose.position.y = self._pose["y"]
        msg.pose.pose.position.z = 0.0
        yaw = self._pose["yaw"]
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = (math.radians(5.0) ** 2)
        return msg

    def _tick(self) -> None:
        if self._got_amcl:
            self.get_logger().info("localization ready")
            rclpy.shutdown()
            return
        if time.monotonic() > self._deadline:
            self.get_logger().error("timeout: no /amcl_pose; check /scan and pose")
            rclpy.shutdown()
            return
        now = time.monotonic()
        if now < self._next_publish:
            return
        self._next_publish = now + self._interval
        for _ in range(5):
            self._pub.publish(self._make_msg())
            time.sleep(0.1)
        self.get_logger().info("published /initialpose burst")


def main() -> None:
    map_yaml = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VOICE_NAV_MAP", "")
    pose = load_pose_from_yaml(map_yaml) or default_pose()
    print(
        f"[initialpose] x={pose['x']} y={pose['y']} yaw={pose['yaw']} yaml={map_yaml}",
        flush=True,
    )
    try:
        max_sec = float(os.environ.get("VOICE_NAV_INIT_RETRY_SEC", "90"))
        interval = float(os.environ.get("VOICE_NAV_INIT_RETRY_INTERVAL", "5"))
    except ValueError:
        max_sec, interval = 90.0, 5.0

    rclpy.init()
    node = InitialPosePublisher(pose, max_sec=max_sec, interval_sec=interval)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
