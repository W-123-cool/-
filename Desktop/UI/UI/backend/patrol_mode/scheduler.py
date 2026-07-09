"""PC 本地时间排班。"""
from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any, Optional

from patrol_mode.config import _SCHEDULES_FILE, ensure_data_dir
from patrol_mode.models import ScheduleEntry

_WEEKDAY_MAP = {
    0: "Mon",
    1: "Tue",
    2: "Wed",
    3: "Thu",
    4: "Fri",
    5: "Sat",
    6: "Sun",
}


def _parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return time(h, m)


def _in_window(now: datetime, start: time, end: time) -> bool:
    t = now.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def load_schedules() -> list[ScheduleEntry]:
    ensure_data_dir()
    if not _SCHEDULES_FILE.is_file():
        default = [
            ScheduleEntry(
                name="night_security",
                enabled=False,
                start="22:00",
                end="06:00",
                mode="patrol",
            )
        ]
        save_schedules(default)
        return default
    try:
        raw = json.loads(_SCHEDULES_FILE.read_text(encoding="utf-8"))
        items = raw.get("schedules") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        return [ScheduleEntry.from_dict(x) for x in items if isinstance(x, dict)]
    except Exception:
        return []


def save_schedules(entries: list[ScheduleEntry]) -> None:
    ensure_data_dir()
    payload = {"schedules": [e.to_dict() for e in entries]}
    _SCHEDULES_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def active_schedule(now: Optional[datetime] = None) -> Optional[ScheduleEntry]:
    now = now or datetime.now()
    wd = _WEEKDAY_MAP.get(now.weekday(), "")
    for entry in load_schedules():
        if not entry.enabled:
            continue
        if entry.weekdays and wd not in entry.weekdays:
            continue
        try:
            if _in_window(now, _parse_hhmm(entry.start), _parse_hhmm(entry.end)):
                return entry
        except (ValueError, IndexError):
            continue
    return None
