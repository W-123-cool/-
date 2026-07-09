"""GUARD centering + PATROL visual chase (from person_tracker 6.30)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def compute_error_x(bbox_center_x: float, image_width: int) -> float:
    """Normalized horizontal error in [-1, 1]. Positive means target is on the right."""
    image_center_x = image_width / 2.0
    half_width = image_width / 2.0
    if half_width <= 0:
        return 0.0
    return (bbox_center_x - image_center_x) / half_width


def compute_y2_ratio(bbox: tuple[int, int, int, int], image_height: int) -> float:
    """Normalized bbox bottom edge y in [0, 1]; larger means closer to screen bottom."""
    if image_height <= 0:
        return 0.0
    _, _, _, y2 = bbox
    return max(0.0, min(1.0, y2 / float(image_height)))


def compute_angular_z(
    error_x: float,
    kp: float,
    dead_zone: float,
    max_angular_z: float,
    *,
    invert: bool = False,
) -> float:
    """Compute angular.z for keeping the target centered horizontally."""
    if abs(error_x) < dead_zone:
        return 0.0
    sign = 1.0 if invert else -1.0
    angular_z = sign * kp * error_x
    return max(-max_angular_z, min(max_angular_z, angular_z))


def control_state_name(error_x: float, dead_zone: float, has_target: bool, lost: bool) -> str:
    if lost or not has_target:
        return "lost"
    if abs(error_x) < dead_zone:
        return "centered"
    return "tracking"


@dataclass
class ChaseConfig:
    """Visual chase: align when |err| >= align_dead_zone, full-speed forward when centered."""

    kp: float = 0.55
    dead_zone: float = 0.05
    align_dead_zone: float = 0.10
    max_angular_z: float = 0.35
    stop_y2_ratio: float = 0.65
    max_linear_x: float = 0.30
    invert_angular: bool = False

    @classmethod
    def from_env(cls) -> ChaseConfig:
        invert = os.environ.get("PATROL_GUARD_INVERT_ANGULAR", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        return cls(
            kp=float(os.environ.get("PATROL_TRACK_CHASE_KP", os.environ.get("PATROL_GUARD_KP", "0.55"))),
            dead_zone=float(os.environ.get("PATROL_TRACK_CHASE_ENTER_DEAD", "0.05")),
            align_dead_zone=float(os.environ.get("PATROL_TRACK_CHASE_REALIGN_DEAD", "0.10")),
            max_angular_z=float(
                os.environ.get("PATROL_TRACK_CHASE_MAX_WZ", os.environ.get("PATROL_GUARD_MAX_ANGULAR_Z", "0.35"))
            ),
            stop_y2_ratio=float(os.environ.get("PATROL_TRACK_STOP_Y2_RATIO", "0.65")),
            max_linear_x=float(os.environ.get("PATROL_TRACK_MAX_LINEAR_MPS", "0.30")),
            invert_angular=invert,
        )


def compute_linear_x(y2_ratio: float, config: ChaseConfig) -> tuple[float, str]:
    """Stop when bbox bottom reaches stop_y2_ratio; otherwise chase at full speed."""
    if y2_ratio >= config.stop_y2_ratio:
        return 0.0, "stopped_close"
    return config.max_linear_x, "chase"


@dataclass
class ControlOutput:
    error_x: float = 0.0
    y2_ratio: float = 0.0
    angular_z: float = 0.0
    linear_x: float = 0.0
    control_state: str = "lost"


class ChaseController:
    """Align-then-chase: turn to center target, then drive forward until y2 stop threshold."""

    def reset(self) -> None:
        pass

    def compute(
        self,
        bbox: tuple[int, int, int, int],
        bbox_center_x: float,
        image_width: int,
        image_height: int,
        config: ChaseConfig,
        *,
        lost: bool = False,
        obstacle_block_forward: bool = False,
    ) -> ControlOutput:
        if lost:
            return ControlOutput(control_state="lost")

        error_x = compute_error_x(bbox_center_x, image_width)
        y2_ratio = compute_y2_ratio(bbox, image_height)
        angular_z = compute_angular_z(
            error_x,
            config.kp,
            config.dead_zone,
            config.max_angular_z,
            invert=config.invert_angular,
        )

        if abs(error_x) >= config.align_dead_zone:
            return ControlOutput(
                error_x=error_x,
                y2_ratio=y2_ratio,
                angular_z=angular_z,
                linear_x=0.0,
                control_state="align",
            )

        linear_x, distance_state = compute_linear_x(y2_ratio, config)
        if obstacle_block_forward:
            linear_x = 0.0
            if distance_state == "chase":
                distance_state = "obstacle_block"

        if distance_state == "stopped_close":
            control_state = "stopped_close"
        elif abs(error_x) < config.dead_zone and linear_x == 0.0:
            control_state = "centered"
        else:
            control_state = distance_state

        return ControlOutput(
            error_x=error_x,
            y2_ratio=y2_ratio,
            angular_z=angular_z,
            linear_x=linear_x,
            control_state=control_state,
        )
