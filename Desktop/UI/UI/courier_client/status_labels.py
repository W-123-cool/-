"""任务状态与机器人状态的中英文展示（与 user_client 保持一致）。"""
from __future__ import annotations

from typing import Optional

TASK_STATUS_CN: dict[str, str] = {
    "pending_delivery": "待投件",
    "delivering": "送货中",
    "await_pickup": "待取货",
    "completed": "已完成",
}

ROBOT_STATE_CN: dict[str, str] = {
    "idle": "初态",
    "pending_delivery": "待投件",
    "delivering": "送货中",
    "await_pickup": "待取货",
    "returning": "返回中",
}


def fmt_task_status(code: Optional[str]) -> str:
    c = (code or "").strip()
    cn = TASK_STATUS_CN.get(c, "未知")
    return f"{c}（{cn}）"


def fmt_robot_state(code: Optional[str]) -> str:
    c = (code or "").strip()
    cn = ROBOT_STATE_CN.get(c, "未知")
    return f"{c}（{cn}）"
