"""巡逻模式数据模型。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class PatrolModeSwitch(str, Enum):
    OFF = "off"
    ON = "on"


class PatrolSubState(str, Enum):
    RETURN_PREP = "return_prep"
    GUARD = "guard"
    GUARD_VIEW_TRACK = "guard_view_track"
    PATROL = "patrol"
    TRACK = "track"
    END_RETURN = "end_return"
    WAIT_TASK = "wait_task"
    GUARD_TIMER = "guard_timer"


class GuardPhase(str, Enum):
    IDLE = "idle"
    MANUAL_ROTATING = "manual_rotating"
    VIEW_TRACKING = "view_tracking"


class FirstMode(str, Enum):
    GUARD = "guard"
    PATROL = "patrol"


@dataclass
class PatrolTaskConfig:
    first_mode: str = FirstMode.GUARD.value
    patrol_rounds: int = 1
    guard_between_min: int = 5
    guard_yaw: float = 0.134
    plan_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatrolTaskConfig:
        return cls(
            first_mode=str(data.get("first_mode", FirstMode.GUARD.value)),
            patrol_rounds=int(data.get("patrol_rounds", 1)),
            guard_between_min=int(data.get("guard_between_min", 5)),
            guard_yaw=float(data.get("guard_yaw", 0.134)),
            plan_dir=str(data.get("plan_dir", "") or ""),
        )


@dataclass
class ScheduleEntry:
    name: str
    enabled: bool = True
    start: str = "22:00"
    end: str = "06:00"
    weekdays: list[str] = field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    mode: str = "patrol"
    patrol_plan: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleEntry:
        return cls(
            name=str(data.get("name", "unnamed")),
            enabled=bool(data.get("enabled", True)),
            start=str(data.get("start", "22:00")),
            end=str(data.get("end", "06:00")),
            weekdays=list(data.get("weekdays") or []),
            mode=str(data.get("mode", "patrol")),
            patrol_plan=str(data.get("patrol_plan", "default")),
        )


@dataclass
class PatrolPreemptSnapshot:
    robot_state: str = "idle"
    delivery_goal_room: str = ""
    task_request_id: str = ""
    was_delivering: bool = False
    was_await_pickup: bool = False
    tour_was_active: bool = False
    tour_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatrolPreemptSnapshot:
        return cls(
            robot_state=str(data.get("robot_state", "idle")),
            delivery_goal_room=str(data.get("delivery_goal_room", "")),
            task_request_id=str(data.get("task_request_id", "")),
            was_delivering=bool(data.get("was_delivering")),
            was_await_pickup=bool(data.get("was_await_pickup")),
            tour_was_active=bool(data.get("tour_was_active")),
            tour_id=str(data.get("tour_id", "")),
        )


@dataclass
class PatrolRuntimeState:
    mode_switch: PatrolModeSwitch = PatrolModeSwitch.OFF
    sub_state: PatrolSubState = PatrolSubState.RETURN_PREP
    message: str = ""
    patrol_epoch: int = 0
    rounds_done: int = 0
    guard_timer_deadline_mono: Optional[float] = None
    wait_task_deadline_mono: Optional[float] = None
    mock_nav_deadline_mono: Optional[float] = None
    schedule_active_name: str = ""
    manual_block_auto_enter: bool = False
    manual_enter_until_end: bool = False
    entered_via: str = "manual"
    preempt: Optional[PatrolPreemptSnapshot] = None
    task: PatrolTaskConfig = field(default_factory=PatrolTaskConfig)
    track_phase: str = "idle"
    track_resume_index: Optional[int] = None
    track_resume_label: str = ""
    nearest_wp_index: Optional[int] = None
    nearest_wp_label: str = ""
    nearest_wp_distance_m: Optional[float] = None
    last_nearest_wp_mono: Optional[float] = None
    guard_phase: str = GuardPhase.IDLE.value

    def to_dict(self) -> dict[str, Any]:
        d = {
            "mode_switch": self.mode_switch.value,
            "sub_state": self.sub_state.value,
            "guard_phase": self.guard_phase,
            "message": self.message,
            "patrol_epoch": self.patrol_epoch,
            "rounds_done": self.rounds_done,
            "schedule_active_name": self.schedule_active_name,
            "manual_block_auto_enter": self.manual_block_auto_enter,
            "manual_enter_until_end": self.manual_enter_until_end,
            "entered_via": self.entered_via,
            "task": self.task.to_dict(),
            "preempt": self.preempt.to_dict() if self.preempt else None,
            "track_phase": self.track_phase,
            "track_resume_index": self.track_resume_index,
            "track_resume_label": self.track_resume_label,
            "nearest_wp": {
                "index": self.nearest_wp_index,
                "label": self.nearest_wp_label,
                "distance_m": self.nearest_wp_distance_m,
            },
        }
        return d
