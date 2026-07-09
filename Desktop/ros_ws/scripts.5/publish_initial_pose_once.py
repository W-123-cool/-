#!/usr/bin/python3
"""Publish /initialpose once via rclpy (avoids ros2 topic pub blocking in Foxy)."""
from __future__ import annotations

import math
import sys
import time

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node


def publish_once(x: float, y: float, qz: float, qw: float, retries: int = 3) -> bool:
    rclpy.init()
    node = Node("publish_initial_pose_once")
    pub = node.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)

    msg = PoseWithCovarianceStamped()
    msg.header.frame_id = "map"
    msg.pose.pose.position.x = x
    msg.pose.pose.position.y = y
    msg.pose.pose.orientation.z = qz
    msg.pose.pose.orientation.w = qw
    msg.pose.covariance[0] = 0.25
    msg.pose.covariance[7] = 0.25
    msg.pose.covariance[35] = math.radians(5.0) ** 2

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and pub.get_subscription_count() == 0:
        rclpy.spin_once(node, timeout_sec=0.1)

    ok = False
    for attempt in range(retries):
        msg.header.stamp = node.get_clock().now().to_msg()
        pub.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.1)
        time.sleep(0.3)
        ok = True
        print(f"[initialpose] published attempt {attempt + 1}/{retries}", flush=True)

    node.destroy_node()
    rclpy.shutdown()
    return ok


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: publish_initial_pose_once.py X Y QZ QW",
            file=sys.stderr,
        )
        return 2
    x, y, qz, qw = (float(v) for v in sys.argv[1:5])
    if publish_once(x, y, qz, qw):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
