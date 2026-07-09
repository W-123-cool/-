"""Knowledge-base Q&A helpers: intro text and floor labels."""
from __future__ import annotations

from typing import Any


_FLOOR_LABEL = {
    "1F": "\u4e00\u697c",
    "2F": "\u4e8c\u697c",
    "3F": "\u4e09\u697c",
}


def floor_label(floor_id: str) -> str:
    fid = str(floor_id or "").strip()
    return _FLOOR_LABEL.get(fid, fid)


def format_intro(room: dict[str, Any], *, current_floor: str = "?") -> str:
    """Spoken introduction for a room (from KB fields)."""
    name = str(room.get("name") or room.get("id") or "")
    rid = str(room.get("id") or "")
    floor = floor_label(str(room.get("floor") or ""))
    detail = str(room.get("intro_detail") or "").strip()
    short = str(room.get("intro_short") or "").strip()
    body = detail or short or f"{floor}{rid}{name}\u3002"
    if current_floor not in ("?", "") and str(room.get("floor")) != current_floor:
        return f"\u60a8\u5f53\u524d\u5728{floor_label(current_floor)}\uff0c{name}\u5728{floor}\u3002{body}"
    return body


def format_floor_list(rooms: list[dict[str, Any]], *, floor_filter: str | None = None) -> str:
    if floor_filter:
        fl = floor_filter.upper()
        rooms = [r for r in rooms if str(r.get("floor", "")).upper().startswith(fl[:1])]
    if not rooms:
        return "\u6682\u65e0\u623f\u95f4\u4fe1\u606f\u3002"
    parts = []
    for room in rooms[:10]:
        rid = room.get("id", "")
        name = room.get("name", rid)
        floor = floor_label(str(room.get("floor", "")))
        parts.append(f"{name}({floor}{rid})")
    return "\u672c\u5c42\u6709\uff1a" + "\u3001".join(parts) + "\u3002"
