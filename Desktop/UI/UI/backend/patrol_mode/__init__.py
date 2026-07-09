"""保安/总控巡逻模式（P1a：PC 状态机 + Web + 排班 + 业务互斥）。"""

from patrol_mode.service import get_patrol_service

__all__ = ["get_patrol_service"]
