"""巡逻计划加载（patrol_planner JSON / manifest）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from patrol_mode.config import DEFAULT_PATROL_OUT, ensure_data_dir

_SELECTED_FILE = ensure_data_dir() / "selected_plan.json"


def _patrol_root(custom: Optional[str] = None) -> Path:
    if custom:
        p = Path(custom).expanduser().resolve()
        if p.is_file():
            return p.parent.parent if p.name.endswith(".json") else p
        return p
    if DEFAULT_PATROL_OUT.is_dir():
        return DEFAULT_PATROL_OUT.resolve()
    return (Path(__file__).resolve().parent.parent.parent.parent / "map" / "patrol_out").resolve()


def list_plan_catalog(root: Optional[str] = None) -> list[dict[str, Any]]:
    base = _patrol_root(root)
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out

    seen: set[str] = set()
    for json_path in sorted(base.rglob("patrol_*.json")):
        key = str(json_path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            plan = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(plan, dict) or "waypoints" not in plan:
            continue
        stem = json_path.stem
        overlay = _find_overlay(base, json_path, plan)
        out.append(
            {
                "id": f"{json_path.parent.name}/{json_path.name}",
                "path": str(json_path.resolve()),
                "floor": plan.get("floor", ""),
                "map_yaml": plan.get("map_yaml", ""),
                "waypoint_count": len(plan.get("waypoints") or []),
                "route_length_m": plan.get("route_length_m"),
                "overlay_png": str(overlay) if overlay else "",
                "generation": plan.get("generation", ""),
            }
        )
    return out


def _find_overlay(base: Path, json_path: Path, plan: dict[str, Any]) -> Optional[Path]:
    map_yaml = str(plan.get("map_yaml", "") or "")
    stem = Path(map_yaml).stem if map_yaml else json_path.stem.replace("patrol_", "")
    for pattern in (f"overlay_{stem}_patrol_*.png", f"overlay_*{stem}*.png"):
        hits = list(json_path.parent.glob(pattern)) or list(base.rglob(pattern))
        if hits:
            return hits[0].resolve()
    return None


def load_plan_by_path(path: str) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"计划文件不存在: {p}")
    plan = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("计划 JSON 格式错误")
    plan["_path"] = str(p)
    return plan


def load_selected_plan() -> Optional[dict[str, Any]]:
    if not _SELECTED_FILE.is_file():
        return None
    try:
        meta = json.loads(_SELECTED_FILE.read_text(encoding="utf-8"))
        path = str(meta.get("path", "") or "")
        if not path:
            return None
        plan = load_plan_by_path(path)
        plan["_selected_id"] = meta.get("id", "")
        return plan
    except Exception:
        return None


def save_selected_plan(plan_path: str, plan_id: str = "") -> dict[str, Any]:
    p = Path(plan_path).expanduser().resolve()
    plan = load_plan_by_path(str(p))
    payload = {"path": str(p), "id": plan_id or f"{p.parent.name}/{p.name}"}
    _SELECTED_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    plan["_selected_id"] = payload["id"]
    return plan


def ordered_waypoints(plan: dict[str, Any]) -> list[dict[str, Any]]:
    wps = {int(w["index"]): w for w in (plan.get("waypoints") or []) if "index" in w}
    order = plan.get("route_order") or sorted(wps.keys())
    result: list[dict[str, Any]] = []
    for idx in order:
        wp = wps.get(int(idx))
        if wp:
            result.append(dict(wp))
    return result


def plan_preview_payload(plan: dict[str, Any]) -> dict[str, Any]:
    overlay = str(plan.get("_overlay") or "")
    if not overlay and plan.get("_path"):
        root = Path(str(plan["_path"])).parent
        o = _find_overlay(root.parent, Path(plan["_path"]), plan)
        overlay = str(o) if o else ""
    return {
        "floor": plan.get("floor"),
        "map_yaml": plan.get("map_yaml"),
        "anchor": plan.get("anchor"),
        "waypoints": plan.get("waypoints"),
        "route_order": plan.get("route_order"),
        "ordered": ordered_waypoints(plan),
        "overlay_png": overlay,
        "path": plan.get("_path"),
    }
