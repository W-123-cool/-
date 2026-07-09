#!/usr/bin/env python3
"""Background Twist publisher used by car_cmd.sh."""

import argparse
import json
import math
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy


@dataclass
class VelocityCommand:
    """Latest velocity command mirrored from the shell script JSON file."""

    vx: float = 0.0
    vy: float = 0.0
    wz: float = 0.0
    ts: Optional[float] = None

    @classmethod
    def from_dict(cls, payload: dict) -> 'VelocityCommand':
        return cls(
            vx=float(payload.get('vx', 0.0)),
            vy=float(payload.get('vy', 0.0)),
            wz=float(payload.get('wz', 0.0)),
            ts=float(payload['ts']) if 'ts' in payload else None,
        )

    def is_zero(self) -> bool:
        return (
            math.isclose(self.vx, 0.0, abs_tol=1e-9)
            and math.isclose(self.vy, 0.0, abs_tol=1e-9)
            and math.isclose(self.wz, 0.0, abs_tol=1e-9)
        )

    def to_twist(self) -> Twist:
        msg = Twist()
        msg.linear.x = self.vx
        msg.linear.y = self.vy
        msg.angular.z = self.wz
        return msg


class CarCmdDaemon(Node):
    """Continuously republishes the latest Twist command at a fixed rate."""

    def __init__(
        self,
        topic: str,
        qos_name: str,
        depth: int,
        rate: float,
        cmd_file: str,
        ready_file: str,
        idle_exit: float,
    ) -> None:
        super().__init__('car_cmd_daemon')

        self._cmd_file = Path(cmd_file)
        self._ready_file = Path(ready_file)
        self._idle_exit = max(0.0, float(idle_exit))
        self._publish_rate = max(1.0, float(rate))
        self._last_cmd_raw: Optional[str] = None
        self._last_cmd_ts: Optional[float] = None
        self._last_activity_monotonic = time.monotonic()
        self._current_cmd = VelocityCommand()
        self._shutdown_requested = False

        qos_profile = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=max(1, int(depth)),
            reliability=self._parse_reliability(qos_name),
        )
        self._publisher = self.create_publisher(Twist, topic, qos_profile)
        self._timer = self.create_timer(1.0 / self._publish_rate, self._on_timer)

        self._remember_existing_command()
        self._write_ready_file()
        self.get_logger().info(
            f'Started daemon: topic={topic} qos={qos_name} depth={depth} rate={self._publish_rate:.1f}Hz '
            f'cmd_file={self._cmd_file}'
        )

    def _parse_reliability(self, qos_name: str) -> ReliabilityPolicy:
        if qos_name == 'best_effort':
            return ReliabilityPolicy.BEST_EFFORT
        if qos_name == 'reliable':
            return ReliabilityPolicy.RELIABLE
        raise ValueError(f'Unsupported qos reliability: {qos_name}')

    def _write_ready_file(self) -> None:
        self._ready_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._ready_file.with_name(f'{self._ready_file.name}.tmp')
        temp_path.write_text(f'{time.time():.6f}\n', encoding='utf-8')
        temp_path.replace(self._ready_file)

    def _remove_ready_file(self) -> None:
        try:
            self._ready_file.unlink()
        except FileNotFoundError:
            return

    def _remember_existing_command(self) -> None:
        try:
            raw = self._cmd_file.read_text(encoding='utf-8')
        except FileNotFoundError:
            return
        except Exception as exc:
            self.get_logger().warn(f'Failed to read existing command file {self._cmd_file}: {exc}')
            return

        self._last_cmd_raw = raw
        try:
            payload = json.loads(raw)
            command = VelocityCommand.from_dict(payload)
        except Exception:
            return
        self._last_cmd_ts = command.ts

    def _load_command_if_updated(self) -> None:
        try:
            raw = self._cmd_file.read_text(encoding='utf-8')
        except FileNotFoundError:
            return
        except Exception as exc:
            self.get_logger().warn(f'Failed to read command file {self._cmd_file}: {exc}')
            return

        if raw == self._last_cmd_raw:
            return

        try:
            payload = json.loads(raw)
            command = VelocityCommand.from_dict(payload)
        except Exception as exc:
            self.get_logger().warn(f'Failed to parse command file {self._cmd_file}: {exc}')
            return

        self._last_cmd_raw = raw
        if command.ts is not None and self._last_cmd_ts is not None and command.ts <= self._last_cmd_ts:
            self.get_logger().warn('Ignoring stale command update.')
            return

        self._last_cmd_ts = command.ts
        self._last_activity_monotonic = time.monotonic()
        self._current_cmd = command
        self.get_logger().info(
            f'Applied command vx={command.vx:.3f} vy={command.vy:.3f} wz={command.wz:.3f} ts={command.ts}'
        )

    def _publish_current(self) -> None:
        self._publisher.publish(self._current_cmd.to_twist())

    def _publish_stop(self, repeats: int = 3, sleep_sec: float = 0.02) -> None:
        stop_msg = Twist()
        for index in range(max(1, repeats)):
            self._publisher.publish(stop_msg)
            if index + 1 < repeats:
                time.sleep(sleep_sec)

    def request_shutdown(self, reason: str) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self.get_logger().info(reason)
        self._current_cmd = VelocityCommand()
        self._publish_stop()
        self._remove_ready_file()
        if rclpy.ok():
            rclpy.shutdown()

    def _on_timer(self) -> None:
        self._load_command_if_updated()
        self._publish_current()

        if self._idle_exit <= 0.0:
            return

        idle_for = time.monotonic() - self._last_activity_monotonic
        if self._current_cmd.is_zero() and idle_for >= self._idle_exit:
            self.request_shutdown(f'Idle timeout reached after {idle_for:.1f}s with zero command.')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Background Twist publisher for car_cmd.sh')
    parser.add_argument('--topic', required=True)
    parser.add_argument('--qos', required=True, choices=['best_effort', 'reliable'])
    parser.add_argument('--depth', required=True, type=int)
    parser.add_argument('--rate', required=True, type=float)
    parser.add_argument('--cmd-file', required=True)
    parser.add_argument('--ready-file', required=True)
    parser.add_argument('--idle-exit', required=True, type=float)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rclpy.init(args=None)
    node = CarCmdDaemon(
        topic=args.topic,
        qos_name=args.qos,
        depth=args.depth,
        rate=args.rate,
        cmd_file=args.cmd_file,
        ready_file=args.ready_file,
        idle_exit=args.idle_exit,
    )

    def _handle_signal(signum, _frame) -> None:
        node.request_shutdown(f'Received signal {signum}, stopping daemon.')

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.request_shutdown('KeyboardInterrupt received, stopping daemon.')
    finally:
        node._remove_ready_file()
        if node.context.ok():
            node._publish_stop(repeats=1, sleep_sec=0.0)
            rclpy.shutdown()
        node.destroy_node()


if __name__ == '__main__':
    main()
