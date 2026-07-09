#!/usr/bin/env python3
"""View Orbbec depth image (use when rqt_image_view fails on 16UC1)."""
import sys

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image


def image_to_depth_u16(msg: Image) -> np.ndarray:
    if msg.encoding not in ("16UC1", "mono16"):
        raise ValueError(f"unsupported encoding: {msg.encoding}")

    row_bytes = msg.width * 2
    if msg.step >= row_bytes:
        buf = np.frombuffer(msg.data, dtype=np.uint8)
        img = buf.reshape(msg.height, msg.step)[:, :row_bytes]
        return img.view(np.uint16).reshape(msg.height, msg.width).copy()

    return np.frombuffer(msg.data, dtype=np.uint16, count=msg.width * msg.height).reshape(
        msg.height, msg.width
    ).copy()


class DepthViewer(Node):
    def __init__(self, topic: str) -> None:
        super().__init__("depth_viewer")
        self.create_subscription(Image, topic, self._on_image, 10)
        self.get_logger().info(f"subscribed {topic}, press q in window to quit")

    def _on_image(self, msg: Image) -> None:
        try:
            depth = image_to_depth_u16(msg)
        except Exception as exc:
            self.get_logger().warn(f"decode failed: {exc}")
            return

        valid = depth > 0
        if not np.any(valid):
            vis = np.zeros(depth.shape, dtype=np.uint8)
            dmin, dmax = 0, 0
        else:
            dmin, dmax = int(depth[valid].min()), int(depth[valid].max())
            span = max(dmax - dmin, 1)
            vis = np.zeros(depth.shape, dtype=np.uint8)
            vis[valid] = ((depth[valid] - dmin) * 255 / span).astype(np.uint8)

        colored = cv2.applyColorMap(vis, cv2.COLORMAP_JET)
        cv2.putText(
            colored,
            f"{msg.width}x{msg.height} mm {dmin}-{dmax}",
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
        )
        cv2.imshow("depth_view", colored)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            rclpy.shutdown()


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "/camera/depth/image_raw"
    rclpy.init()
    node = DepthViewer(topic)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
