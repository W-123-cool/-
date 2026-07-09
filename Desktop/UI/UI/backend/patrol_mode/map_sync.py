"""开巡前地图/楼层与车端心跳对齐检查。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from patrol_mode.config import mock_vehicle_enabled


def _vehicle_snapshot() -> dict[str, Any]:
    try:
        from mqtt_robot_bridge import bridge_enabled, get_bridge

        if bridge_enabled():
            return get_bridge().snapshot()
    except Exception:
        pass
    return {}


def _floor_map_yaml(floor: str) -> str:
    try:
        from vehicle_rooms import ROS_WS

        switcher = ROS_WS / "src/smart_nav_manager/smart_nav_manager/switcher_node.py"
        if switcher.is_file():
            import ast

            text = switcher.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == "FLOOR_MAPS":
                            fm = ast.literal_eval(node.value)
                            if isinstance(fm, dict) and floor in fm:
                                return Path(str(fm[floor])).name
    except Exception:
        pass
    return ""


def check_map_sync(plan: dict[str, Any], hb: Optional[dict[str, Any]] = None) -> tuple[bool, str, dict[str, Any]]:
    plan_yaml = Path(str(plan.get("map_yaml", "") or "")).name
    plan_floor = str(plan.get("floor", "") or "")
    hb = hb if hb is not None else _vehicle_snapshot()

    detail: dict[str, Any] = {
        "plan_map_yaml": plan_yaml,
        "plan_floor": plan_floor,
        "vehicle_floor": hb.get("current_floor"),
        "vehicle_map_yaml": hb.get("current_map_yaml"),
        "mock_vehicle": mock_vehicle_enabled(),
    }

    if mock_vehicle_enabled() and not hb:
        detail["warning"] = "mock 模式：未校验真车地图"
        return True, "mock 模式跳过地图同步（联真车请关闭 mock）", detail

    if not hb:
        return False, "无车端心跳，无法校验地图", detail

    veh_yaml = str(hb.get("current_map_yaml") or "").strip()
    if not veh_yaml and plan_floor:
        veh_yaml = _floor_map_yaml(str(hb.get("current_floor", plan_floor)))
        detail["vehicle_map_yaml_inferred"] = veh_yaml

    if plan_yaml and veh_yaml and Path(plan_yaml).name != Path(veh_yaml).name:
        return (
            False,
            f"地图不一致：计划 {plan_yaml}，车端 {veh_yaml}",
            detail,
        )

    veh_floor = str(hb.get("current_floor", "") or "")
    if plan_floor and veh_floor and plan_floor != veh_floor:
        detail["warning"] = f"楼层提示：计划 {plan_floor}，车端 {veh_floor}（跨层路点将走电梯）"

    return True, "地图校验通过", detail
