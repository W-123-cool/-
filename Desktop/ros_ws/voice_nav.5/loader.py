"""加载房间知识库，并与 switcher_node 导航坐标表校验 id。"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

_PKG = Path(__file__).resolve().parent
ROS_WS = _PKG.parent
KNOWLEDGE_PATH = Path(
    __import__("os").environ.get("VOICE_NAV_KNOWLEDGE", str(ROS_WS / "knowledge" / "rooms.json"))
)
_SWITCHER = ROS_WS / "src" / "smart_nav_manager" / "smart_nav_manager" / "switcher_node.py"


def _parse_assign_dict(source: str, name: str) -> Any:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise ValueError(f"{name} not found")


def load_nav_room_ids() -> set[str]:
    if not _SWITCHER.is_file():
        return set()
    try:
        text = _SWITCHER.read_text(encoding="utf-8")
        rooms = _parse_assign_dict(text, "ROOM_LOCATIONS")
        return {str(k) for k in rooms.keys()}
    except Exception:
        return set()


def load_knowledge() -> dict[str, Any]:
    if not KNOWLEDGE_PATH.is_file():
        return {"rooms": [], "floors": []}
    data = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    rooms = data.get("rooms") or []
    nav_ids = load_nav_room_ids()
    if nav_ids:
        for room in rooms:
            rid = str(room.get("id", ""))
            if room.get("navigable") and rid and rid not in nav_ids:
                room["_warn"] = f"id {rid} 不在 ROOM_LOCATIONS 中，导航可能失败"
    return data


def get_room_by_id(kb: dict[str, Any], room_id: str) -> dict[str, Any] | None:
    rid = str(room_id).strip()
    for room in kb.get("rooms") or []:
        if str(room.get("id", "")) == rid:
            return room
    return None


def format_kb_for_llm(kb: dict[str, Any]) -> str:
    """Serialize full knowledge base for cloud LLM prompt."""
    lines: list[str] = []
    for floor in kb.get("floors") or []:
        fid = str(floor.get("id", ""))
        label = str(floor.get("label", fid))
        summary = str(floor.get("summary", "")).strip()
        lines.append(f"[\u697c\u5c42] {fid} {label}: {summary}")
    for room in kb.get("rooms") or []:
        rid = str(room.get("id", ""))
        name = str(room.get("name", rid))
        floor = str(room.get("floor", ""))
        aliases = ",".join(str(a) for a in (room.get("aliases") or []))
        tags = ",".join(str(t) for t in (room.get("tags") or []))
        short = str(room.get("intro_short", "")).strip()
        detail = str(room.get("intro_detail", "")).strip()
        nav = "\u662f" if room.get("navigable", True) else "\u5426"
        body = detail or short
        lines.append(
            f"[\u623f\u95f4] id={rid} name={name} floor={floor} "
            f"aliases={aliases} tags={tags} navigable={nav}\n{body}"
        )
    return "\n\n".join(lines) if lines else "(\u65e0\u77e5\u8bc6\u5e93\u6570\u636e)"


def list_navigable_rooms(kb: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for room in kb.get("rooms") or []:
        if room.get("navigable", True):
            out.append(room)
    return out
