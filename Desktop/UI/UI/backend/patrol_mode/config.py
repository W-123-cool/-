"""巡逻模式配置（环境变量 + 默认路径）。"""
from __future__ import annotations

import os
from pathlib import Path

_UI_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _UI_ROOT / "data" / "security"
_SCHEDULES_FILE = _DATA_DIR / "schedules.json"
_TASK_CONFIG_FILE = _DATA_DIR / "task_config.json"

DEFAULT_PATROL_OUT = _UI_ROOT.parent.parent / "map" / "patrol_out"
MOCK_WAYPOINT_SEC = float(os.environ.get("PATROL_MOCK_WAYPOINT_SEC", "1.5"))
PATROL_WAYPOINT_TIMEOUT_SEC = float(os.environ.get("PATROL_WAYPOINT_TIMEOUT_SEC", "300"))

ENTRY_ROOM_ID = "100"
HOME_ARRIVE_TOLERANCE_M = float(os.environ.get("PATROL_HOME_TOLERANCE_M", "0.5"))
WAIT_TASK_TIMEOUT_SEC = float(os.environ.get("PATROL_WAIT_TASK_TIMEOUT_SEC", "120"))
MOCK_NAV_SEC = float(os.environ.get("PATROL_MOCK_NAV_SEC", "2.0"))
TICK_INTERVAL_SEC = float(os.environ.get("PATROL_TICK_INTERVAL_SEC", "1.0"))
OPERATOR_PIN = os.environ.get("SECURITY_OPERATOR_PIN", "1234").strip()
TOKEN_TTL_SEC = float(os.environ.get("SECURITY_TOKEN_TTL_SEC", "3600"))

_ALERTS_DIR = _DATA_DIR / "alerts"
_ALERTS_INDEX = _ALERTS_DIR / "index.json"
ALERT_MAX_COUNT = int(os.environ.get("SECURITY_ALERT_MAX_COUNT", "500"))
ALERT_RETENTION_DAYS = int(os.environ.get("SECURITY_ALERT_RETENTION_DAYS", "7"))
ALERT_DEDUP_SEC = float(os.environ.get("SECURITY_ALERT_DEDUP_SEC", "2.0"))
NEAREST_WP_INTERVAL_SEC = float(os.environ.get("PATROL_NEAREST_WP_SEC", "10"))
PERSON_EVENT_COOLDOWN_SEC = float(os.environ.get("PATROL_PERSON_EVENT_COOLDOWN_SEC", "2"))
TRACK_GOAL_HZ = float(os.environ.get("PATROL_TRACK_GOAL_HZ", "10"))
TRACK_MAX_LINEAR_MPS = float(os.environ.get("PATROL_TRACK_MAX_LINEAR_MPS", "0.15"))
PATROL_TRACK_LOST_SEC = float(os.environ.get("PATROL_TRACK_LOST_SEC", "2.5"))
TRACK_REENTRY_COOLDOWN_SEC = float(os.environ.get("PATROL_TRACK_REENTRY_COOLDOWN_SEC", "20"))
PATROL_SCAN_SEC = float(os.environ.get("PATROL_SCAN_SEC", "8"))
PATROL_SNAPSHOT_URL = os.environ.get(
    "PATROL_SNAPSHOT_URL", "http://127.0.0.1:8000/api/security/snapshot"
).strip()
PATROL_CAMERA_STREAM_URL = os.environ.get("PATROL_CAMERA_STREAM_URL", "").strip()
PATROL_UPLOAD_KEY = os.environ.get("PATROL_UPLOAD_KEY", "").strip()


def mock_vision_enabled() -> bool:
    return os.environ.get("SECURITY_MOCK_VISION", "").strip().lower() in ("1", "true", "yes", "on")


def alerts_dir() -> Path:
    _ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    return _ALERTS_DIR


def alerts_index_path() -> Path:
    alerts_dir()
    return _ALERTS_INDEX


def mock_vehicle_enabled() -> bool:
    if os.environ.get("SECURITY_MOCK_VEHICLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        from mqtt_robot_bridge import bridge_enabled

        return not bridge_enabled()
    except Exception:
        return True


def ensure_data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR
