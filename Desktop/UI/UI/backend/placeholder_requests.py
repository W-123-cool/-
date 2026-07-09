"""
占位符请求扩展位：后续可在此定义新请求类型枚举、校验器，
并在 task_manager / main 路由中挂载，而不修改现有取货主路径。

示例方向：
- 枚举 PlaceholderKind（保洁、巡逻等）
- 与 RobotState 的互斥规则（哪些状态下允许接单）
- 独立数据表 placeholder_tasks（与 tasks 并列）
"""
from __future__ import annotations

from enum import Enum


class PlaceholderKind(str, Enum):
    """占位：业务未定时勿在生产启用。"""

    RESERVED = "reserved"


def validate_placeholder_payload(kind: PlaceholderKind, payload: dict) -> tuple[bool, str]:
    """占位校验器接口。"""
    if kind != PlaceholderKind.RESERVED:
        return False, "未知占位类型"
    return True, "ok"
