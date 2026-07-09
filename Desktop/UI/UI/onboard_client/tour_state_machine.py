"""导览业务状态机（六态，与 backend tour_phases 对齐；api 模式下以后端轮询为准）。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum, auto
from typing import Optional


class NaviState(Enum):
    IDLE = auto()
    WAITING_VOICE = auto()
    NAVIGATING = auto()
    AT_DEST = auto()
    HOLDING = auto()
    RETURNING = auto()


PHASE_BY_STATE: dict[NaviState, str] = {
    NaviState.IDLE: "idle",
    NaviState.WAITING_VOICE: "waiting_voice",
    NaviState.NAVIGATING: "navigating",
    NaviState.AT_DEST: "at_dest",
    NaviState.HOLDING: "holding",
    NaviState.RETURNING: "returning",
}

STATE_BY_PHASE: dict[str, NaviState] = {
    "idle": NaviState.IDLE,
    "waiting_voice": NaviState.WAITING_VOICE,
    "navigating": NaviState.NAVIGATING,
    "at_dest": NaviState.AT_DEST,
    "holding": NaviState.HOLDING,
    "returning": NaviState.RETURNING,
    "arrived": NaviState.HOLDING,
}


class NaviStateMachine:
    STATE_LABELS = {
        NaviState.IDLE: "初态",
        NaviState.WAITING_VOICE: "待按键语音输入",
        NaviState.NAVIGATING: "导览中",
        NaviState.AT_DEST: "抵达目的地",
        NaviState.HOLDING: "原地待机",
        NaviState.RETURNING: "返回起点",
    }

    def __init__(self) -> None:
        self._state = NaviState.IDLE
        self._target = ""
        self._message = ""
        self._ui_locked = False
        self._deadline_remaining: Optional[float] = None
        self._logs: list[str] = []
        self._callbacks: dict[str, list] = {
            "on_state_change": [],
            "on_log": [],
        }

    @property
    def state(self) -> NaviState:
        return self._state

    @property
    def state_label(self) -> str:
        return str(self.STATE_LABELS.get(self._state, "未知"))

    @property
    def target(self) -> str:
        return self._target

    @property
    def message(self) -> str:
        return self._message

    @property
    def ui_locked(self) -> bool:
        return self._ui_locked

    @property
    def deadline_remaining(self) -> Optional[float]:
        return self._deadline_remaining

    @property
    def logs(self) -> list[str]:
        return list(self._logs)

    def is_idle(self) -> bool:
        return self._state == NaviState.IDLE

    def is_holding(self) -> bool:
        return self._state == NaviState.HOLDING

    def tour_busy(self) -> bool:
        return self._state != NaviState.IDLE

    def on(self, event: str, callback) -> None:
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit_state_change(self) -> None:
        for cb in self._callbacks["on_state_change"]:
            cb(self._state, self.state_label)

    def _emit_log(self, msg: str) -> None:
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self._logs.append(entry)
        for cb in self._callbacks["on_log"]:
            cb(entry)

    def _transition_to(self, new_state: NaviState, *, log: bool = True) -> None:
        old_label = self.state_label
        self._state = new_state
        if log:
            self._emit_log(f"状态切换: {old_label} → {self.state_label}")
        self._emit_state_change()

    def sync_from_authority(
        self,
        phase: str,
        room: str = "",
        message: str = "",
        *,
        ui_locked: bool = False,
        deadline_remaining: Optional[float] = None,
    ) -> None:
        ph = str(phase or "idle").strip().lower()
        new_state = STATE_BY_PHASE.get(ph, NaviState.IDLE)
        changed = new_state != self._state or room != self._target
        self._target = room.strip()
        self._message = message.strip()
        self._ui_locked = ui_locked
        self._deadline_remaining = deadline_remaining
        if new_state == NaviState.IDLE:
            self._target = ""
            self._message = ""
            self._ui_locked = False
            self._deadline_remaining = None
        if changed:
            self._transition_to(new_state, log=True)
        elif message and message != self._message:
            self._message = message
            self._emit_state_change()

    def enter_waiting_voice(self) -> tuple[bool, str]:
        if self._state != NaviState.IDLE:
            return False, f"当前 [{self.state_label}] 不可进入待语音"
        self._transition_to(NaviState.WAITING_VOICE)
        self._emit_log("已进入待按键语音输入")
        return True, "已进入待按键语音输入"

    def discard_waiting_voice(self) -> tuple[bool, str]:
        if self._state != NaviState.WAITING_VOICE:
            return False, "当前不在待语音态"
        self._target = ""
        self._transition_to(NaviState.IDLE)
        return True, "已取消待语音输入"

    def begin_navigating(self, target: str) -> tuple[bool, str]:
        if self._state not in (NaviState.IDLE, NaviState.WAITING_VOICE):
            return False, f"当前 [{self.state_label}] 不可开始导览"
        target = target.strip()
        if not target:
            return False, "目标不能为空"
        self._target = target
        self._transition_to(NaviState.NAVIGATING)
        self._emit_log(f"开始导览 → {target}")
        return True, "导览已开始"

    def on_arrived(self) -> tuple[bool, str]:
        if self._state != NaviState.NAVIGATING:
            return False, f"当前 [{self.state_label}] 不能到站"
        self._transition_to(NaviState.HOLDING)
        self._emit_log(f"已到达 [{self._target}]，进入原地待机")
        return True, "已进入原地待机"

    def holding_cancel_confirm(self) -> tuple[bool, str]:
        if self._state not in (NaviState.HOLDING, NaviState.NAVIGATING):
            return False, f"当前 [{self.state_label}] 不可确认取消"
        self._transition_to(NaviState.RETURNING)
        self._emit_log("确认取消导览，返回起点")
        return True, "已进入返回起点"

    def on_return_home_complete(self) -> tuple[bool, str]:
        if self._state != NaviState.RETURNING:
            return False, f"当前 [{self.state_label}] 不在返回起点"
        self._emit_log("已回到起点，导览结束")
        self._target = ""
        self._message = ""
        self._transition_to(NaviState.IDLE)
        return True, "导览已结束"

    def enter_holding_from_stop(self) -> tuple[bool, str]:
        if self._state != NaviState.NAVIGATING:
            return False, f"当前 [{self.state_label}] 不可截停"
        self._transition_to(NaviState.HOLDING)
        self._emit_log("导览已截停，原地待机")
        return True, "已进入原地待机"

    def touch_activity(self) -> None:
        """本地演练：刷新待机计时显示（权威计时在后端）。"""
        if self._state in (NaviState.WAITING_VOICE, NaviState.HOLDING):
            self._deadline_remaining = 120.0
