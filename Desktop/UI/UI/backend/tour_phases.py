"""导览主状态相位（后端权威，onboard 镜像同名 phase 字符串）。"""
from __future__ import annotations

PHASE_IDLE = "idle"
PHASE_WAITING_VOICE = "waiting_voice"
PHASE_NAVIGATING = "navigating"
PHASE_AT_DEST = "at_dest"
PHASE_HOLDING = "holding"
PHASE_RETURNING = "returning"

# 旧 phase 兼容
LEGACY_ARRIVED = "arrived"

ACTIVE_PHASES = frozenset(
    {
        PHASE_WAITING_VOICE,
        PHASE_NAVIGATING,
        PHASE_AT_DEST,
        PHASE_HOLDING,
        PHASE_RETURNING,
    }
)

PHASE_LABEL_CN: dict[str, str] = {
    PHASE_IDLE: "初态",
    PHASE_WAITING_VOICE: "待按键语音输入",
    PHASE_NAVIGATING: "导览中",
    PHASE_AT_DEST: "抵达目的地",
    PHASE_HOLDING: "原地待机",
    PHASE_RETURNING: "返回起点",
    LEGACY_ARRIVED: "抵达目的地",
}

MOVING_NAV_STATES = frozenset(
    {
        "GOING_TO_ELEVATOR",
        "WAITING_ELEVATOR",
        "SWITCHING_MAP",
        "NAVIGATING_TO_ROOM",
    }
)

HOLDING_TIMEOUT_SEC = 120.0
WAITING_VOICE_TIMEOUT_SEC = 120.0


def normalize_phase(phase: str) -> str:
    p = str(phase or "").strip().lower()
    if p == LEGACY_ARRIVED:
        return PHASE_HOLDING
    return p or PHASE_IDLE


def phase_label_cn(phase: str) -> str:
    return PHASE_LABEL_CN.get(normalize_phase(phase), "未知")


def is_tour_busy(phase: str) -> bool:
    return normalize_phase(phase) in ACTIVE_PHASES


def vehicle_nav_moving(nav_state: str) -> bool:
    return str(nav_state or "").strip() in MOVING_NAV_STATES
