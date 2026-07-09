"""从主 ros_ws switcher_node 同步房间表（与真车 ROOM_LOCATIONS 一致）。"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

_SWITCHER_REL = Path("src/smart_nav_manager/smart_nav_manager/switcher_node.py")


def resolve_ros_ws() -> Path:
    """定位主 ros_ws（与 ai_car_resolve_ros_ws 候选路径一致）。"""
    def _valid(p: Path) -> bool:
        try:
            p = p.resolve()
        except OSError:
            return False
        return (p / "car_cmd.sh").is_file() or (p / _SWITCHER_REL).is_file()

    candidates: list[Path] = []
    env = os.environ.get("AI_CAR_ROS_WS", "").strip()
    if env:
        candidates.append(Path(env))

    home = Path.home()
    candidates.extend(
        [
            home / "Desktop" / "rock_ws" / "ros_ws",
            home / "rock_ws" / "ros_ws",
        ]
    )

    ui_root = Path(__file__).resolve().parent.parent
    for ancestor in (ui_root.parent, ui_root.parent.parent):
        candidates.append(ancestor / "ros_ws")
        candidates.append(ancestor / "rock_ws" / "ros_ws")

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if _valid(candidate):
            return candidate.resolve()

    return home / "Desktop" / "rock_ws" / "ros_ws"


ROS_WS = resolve_ros_ws()
_SWITCHER = ROS_WS / _SWITCHER_REL

# 解析失败时的回退（my_map6 1F + my_map8 2F）
_FALLBACK_ROOMS: dict[str, dict[str, Any]] = {
    "100": {"floor": "1F", "x": -0.05135, "y": 0.5785, "yaw": 0.52154},
    "101": {"floor": "1F", "x": 0.782, "y": 4.39, "yaw": 0.00554},
    "102": {"floor": "1F", "x": 3.46, "y": 0.261, "yaw": 0.00393},
    "103": {"floor": "1F", "x": 3.22, "y": 6.06, "yaw": 0.00406},
    "104": {"floor": "1F", "x": 5.97, "y": 1.86, "yaw": 0.00254},
    "105": {"floor": "1F", "x": 5.475, "y": 3.725, "yaw": 0.5215},
    "200": {"floor": "2F", "x": 0.04172, "y": 0.00707, "yaw": -0.70409},
    "201": {"floor": "2F", "x": -1.97, "y": 1.34, "yaw": 0.00178},
    "202": {"floor": "2F", "x": 3.05, "y": -1.77, "yaw": 0.00196},
    "203": {"floor": "2F", "x": 1.36, "y": 2.81, "yaw": 0.00144},
    "204": {"floor": "2F", "x": -0.264, "y": 3.67, "yaw": 0.00168},
}
_FALLBACK_NON_DELIVERY = ("100", "105", "200")


def _parse_assign_dict(source: str, name: str) -> Any:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise ValueError(f"{name} not found in switcher_node.py")


def load_room_locations() -> dict[str, dict[str, Any]]:
    if not _SWITCHER.is_file():
        return dict(_FALLBACK_ROOMS)
    try:
        text = _SWITCHER.read_text(encoding="utf-8")
        rooms = _parse_assign_dict(text, "ROOM_LOCATIONS")
        if isinstance(rooms, dict) and rooms:
            return {str(k): dict(v) for k, v in rooms.items()}
    except Exception:
        pass
    return dict(_FALLBACK_ROOMS)


ROOM_LOCATIONS: dict[str, dict[str, Any]] = load_room_locations()
NON_DELIVERY_ROOM_IDS: tuple[str, ...] = _FALLBACK_NON_DELIVERY
try:
    if _SWITCHER.is_file():
        text = _SWITCHER.read_text(encoding="utf-8")
        nd = _parse_assign_dict(text, "NON_DELIVERY_ROOM_IDS")
        if isinstance(nd, (list, tuple)):
            NON_DELIVERY_ROOM_IDS = tuple(str(x) for x in nd)
except Exception:
    pass

TOUR_ROOM_IDS: tuple[str, ...] = tuple(
    k for k in sorted(ROOM_LOCATIONS.keys()) if k not in NON_DELIVERY_ROOM_IDS
)
ENTRY_ROOM_ID = "100"
FLOOR_LABELS = {"1F": "1楼", "2F": "2楼"}


def list_building_catalog() -> dict[str, Any]:
    """供导览 UI：按楼层分组的房间列表。"""
    floors: dict[str, list[dict[str, str]]] = {}
    for rid in sorted(ROOM_LOCATIONS.keys()):
        info = ROOM_LOCATIONS[rid]
        fl = str(info.get("floor", "?"))
        floors.setdefault(fl, []).append(
            {
                "id": rid,
                "floor": fl,
                "floor_label": FLOOR_LABELS.get(fl, fl),
                "tour_selectable": rid in TOUR_ROOM_IDS,
            }
        )
    return {
        "floors": [
            {
                "id": fid,
                "label": FLOOR_LABELS.get(fid, fid),
                "rooms": floors[fid],
            }
            for fid in sorted(floors.keys())
        ],
        "tour_room_ids": list(TOUR_ROOM_IDS),
        "non_delivery_room_ids": list(NON_DELIVERY_ROOM_IDS),
        "ros_ws": str(ROS_WS),
        "source": str(_SWITCHER) if _SWITCHER.is_file() else "fallback",
    }
