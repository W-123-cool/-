"""
车载端（机器人）全局状态机 — PC 上为内存模拟，与任务生命周期联动。

状态（与需求对应）：
- IDLE：初态；可接收新取货请求；不可投件。
- PENDING_DELIVERY：待投件；可继续接收新请求；仅允许对「已有待投件任务」投件。
- DELIVERING：送货中；可接收新请求；不可投件。
- AWAIT_PICKUP：待取货；可接收新请求；不可投件。
- RETURNING：返回中；可接收新请求；不可投件。

切换条件：
- IDLE -> PENDING_DELIVERY：出现至少一条待投件任务。
- PENDING_DELIVERY -> DELIVERING：送货员确认投件。
- DELIVERING -> AWAIT_PICKUP：货物送达站点。
- AWAIT_PICKUP -> RETURNING：用户取货成功（密码正确）。
- RETURNING -> IDLE：回初始点完成；若仍有待投件任务则 -> PENDING_DELIVERY。
"""
from __future__ import annotations

from enum import Enum
from threading import Lock
from typing import Callable


class RobotState(str, Enum):
    IDLE = "idle"
    PENDING_DELIVERY = "pending_delivery"
    DELIVERING = "delivering"
    AWAIT_PICKUP = "await_pickup"
    RETURNING = "returning"


class RobotStateMachine:
    """线程安全的单机器人状态（后续可替换为 ROS2 话题/服务回调）。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state: RobotState = RobotState.IDLE
        self._listeners: list[Callable[[RobotState, RobotState], None]] = []

    @property
    def state(self) -> RobotState:
        with self._lock:
            return self._state

    def add_listener(self, fn: Callable[[RobotState, RobotState], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, old: RobotState, new: RobotState) -> None:
        for fn in self._listeners:
            fn(old, new)

    def force_set(self, new: RobotState) -> None:
        """调试或复位。"""
        with self._lock:
            old, self._state = self._state, new
        self._emit(old, new)

    def on_task_pending_created(self) -> None:
        """新建一条「待投件」任务后：若机器人在初态则进入待投件。"""
        with self._lock:
            old = self._state
            if self._state == RobotState.IDLE:
                self._state = RobotState.PENDING_DELIVERY
                new = self._state
            else:
                return  # 不改变状态
        self._emit(old, new)

    def on_courier_confirm_dispatch(self) -> bool:
        """待投件 -> 送货中。"""
        with self._lock:
            if self._state != RobotState.PENDING_DELIVERY:
                return False
            old, self._state = self._state, RobotState.DELIVERING
        self._emit(old, RobotState.DELIVERING)
        return True

    def on_robot_arrived_at_dropoff(self) -> bool:
        """送货中 -> 待取货。"""
        with self._lock:
            if self._state != RobotState.DELIVERING:
                return False
            old, self._state = self._state, RobotState.AWAIT_PICKUP
        self._emit(old, RobotState.AWAIT_PICKUP)
        return True

    def on_user_pickup_success(self) -> bool:
        """待取货 -> 返回中。"""
        with self._lock:
            if self._state != RobotState.AWAIT_PICKUP:
                return False
            old, self._state = self._state, RobotState.RETURNING
        self._emit(old, RobotState.RETURNING)
        return True

    def on_return_home_complete(self, still_has_pending: bool) -> None:
        """返回中 -> 初态或待投件（仍有排队任务时）。"""
        with self._lock:
            old = self._state
            self._state = (
                RobotState.PENDING_DELIVERY if still_has_pending else RobotState.IDLE
            )
            new = self._state
        self._emit(old, new)

    def on_tour_end_begin_return(self) -> bool:
        """导览结束/确认取消 -> 返回中（送货中、待取货不可切入）。"""
        with self._lock:
            if self._state in (RobotState.DELIVERING, RobotState.AWAIT_PICKUP):
                return False
            old, self._state = self._state, RobotState.RETURNING
        self._emit(old, RobotState.RETURNING)
        return True

    def can_courier_dispatch(self) -> bool:
        return self.state == RobotState.PENDING_DELIVERY

    def can_accept_pickup_request(self) -> bool:
        """返回中仍可接单；送货/待取货/返回中（非接单语义）由其它接口约束。"""
        return self._state != RobotState.DELIVERING and self._state != RobotState.AWAIT_PICKUP
