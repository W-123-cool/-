"""从 switcher_node.py 解析楼层与房间配置（AST，不 import ROS）。"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any, Optional

_SWITCHER_REL = Path("src/smart_nav_manager/smart_nav_manager/switcher_node.py")
_TOOL_ROOT = Path(__file__).resolve().parent
_NOVAJOY_ROOT = _TOOL_ROOT.parent.parent  # Desktop


def resolve_switcher_path(explicit: Optional[str] = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"switcher 不存在: {p}")
        return p

    env = os.environ.get("AI_CAR_ROS_WS", "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env) / _SWITCHER_REL)
    candidates.append(_NOVAJOY_ROOT / "ros_ws" / _SWITCHER_REL)

    for p in candidates:
        if p.is_file():
            return p.resolve()

    raise FileNotFoundError(
        "未找到 switcher_node.py，请用 --switcher 指定路径，或设置 AI_CAR_ROS_WS"
    )


def _parse_assign(source: str, name: str) -> Any:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise ValueError(f"{name} not found in switcher_node.py")


def load_switcher_config(switcher_path: Path) -> dict[str, Any]:
    text = switcher_path.read_text(encoding="utf-8")
    floor_maps = _parse_assign(text, "FLOOR_MAPS")
    rooms = _parse_assign(text, "ROOM_LOCATIONS")
    entry = "100"
    try:
        entry = str(_parse_assign(text, "ENTRY_ROOM_ID"))
    except ValueError:
        pass

    if not isinstance(floor_maps, dict) or not floor_maps:
        raise ValueError("FLOOR_MAPS 为空或格式错误")
    if not isinstance(rooms, dict):
        raise ValueError("ROOM_LOCATIONS 格式错误")

    floors = {str(k): str(v) for k, v in floor_maps.items()}
    room_locs = {str(k): dict(v) for k, v in rooms.items()}
    return {
        "floor_maps": floors,
        "room_locations": room_locs,
        "entry_room_id": entry,
        "switcher_path": str(switcher_path.resolve()),
    }


def floor_for_map_yaml(map_yaml_name: str, floor_maps: dict[str, str]) -> Optional[str]:
    name = Path(map_yaml_name).name
    hits = [fl for fl, mf in floor_maps.items() if Path(mf).name == name]
    if not hits:
        return None
    if len(hits) > 1:
        return hits[0]
    return hits[0]


def anchor_for_floor(
    floor: str,
    *,
    entry_room_id: str,
    room_locations: dict[str, dict[str, Any]],
    yaml_initial_pose: Optional[dict[str, float]],
) -> dict[str, Any]:
    """1F 用 ROOM 100；其余楼层用 yaml initial_pose。"""
    if floor == "1F":
        rid = entry_room_id
        if rid not in room_locations:
            raise ValueError(f"1F 锚点房间 {rid} 不在 ROOM_LOCATIONS")
        info = room_locations[rid]
        return {
            "kind": "anchor",
            "id": rid,
            "label": rid,
            "x": float(info["x"]),
            "y": float(info["y"]),
            "yaw": float(info.get("yaw", 0.0)),
            "source": f"room:{rid}",
        }

    if not yaml_initial_pose:
        raise ValueError(f"{floor} 缺少 yaml initial_pose")
    return {
        "kind": "anchor",
        "id": f"{floor}_entry",
        "label": f"{floor}_initial_pose",
        "x": float(yaml_initial_pose["x"]),
        "y": float(yaml_initial_pose["y"]),
        "yaw": float(yaml_initial_pose.get("yaw", 0.0)),
        "source": "yaml:initial_pose",
    }


def validate_floor_maps(floor_maps: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    values = list(floor_maps.values())
    if len(set(values)) < len(values):
        dup = [v for v in set(values) if values.count(v) > 1]
        warnings.append(
            f"FLOOR_MAPS 中多张楼层指向同一地图: {dup}；全楼模式会对同图重复规划"
        )
    return warnings
