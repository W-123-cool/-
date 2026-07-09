"""巡逻规划执行逻辑（CLI / GUI 共用）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from patrol_core import (
    TOOL_VERSION,
    file_sha256,
    load_map,
    plan_patrol_for_map,
    plan_patrol_manual,
    render_overlay,
    write_manifest,
)
from switcher_config import (
    anchor_for_floor,
    floor_for_map_yaml,
    load_switcher_config,
    resolve_switcher_path,
    validate_floor_maps,
)

LogFn = Callable[[str], None]


@dataclass
class PatrolRunOptions:
    command: str  # building | single
    map_dir: Path
    out_dir: Path
    switcher: str = ""
    generation: str = "auto"  # auto | manual
    coverage_mode: str = "full_free"
    max_uncovered_ratio: Optional[float] = None
    sample_step: float = 0.25
    inflate: float = 0.3
    single_map: str = ""
    manual_points: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


@dataclass
class PatrolRunResult:
    ok: bool
    message: str = ""
    plans: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    overlay_paths: list[Path] = field(default_factory=list)
    json_paths: list[Path] = field(default_factory=list)
    manifest_path: Optional[Path] = None
    log_lines: list[str] = field(default_factory=list)


@dataclass
class MapMarkContext:
    """手动标记：单张地图的加载上下文。"""

    key: str
    floor: Optional[str]
    yaml_path: Path
    mapdata: object
    anchor: dict
    anchor_cell: tuple[int, int]


def _log(log: LogFn | None, line: str, lines: list[str]) -> None:
    lines.append(line)
    if log:
        log(line)


def resolve_map_yaml(map_arg: str, map_dir: Path) -> Path:
    p = Path(map_arg)
    if p.is_file():
        return p.resolve()
    candidate = map_dir / map_arg
    if not candidate.suffix:
        candidate = map_dir / f"{map_arg}.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(f"地图不存在: {map_arg} (在 {map_dir})")
    return candidate.resolve()


def list_map_yamls(map_dir: Path) -> list[str]:
    if not map_dir.is_dir():
        return []
    return sorted(p.name for p in map_dir.glob("*.yaml"))


def list_building_floors(cfg: dict) -> list[tuple[str, str]]:
    """[(floor_id, yaml_name), ...] 按 FLOOR_MAPS 顺序。"""
    return [(str(f), str(y)) for f, y in sorted(cfg["floor_maps"].items())]


def resolve_anchor_for_map(
    floor: str | None,
    cfg: dict,
    mapdata,
) -> tuple[dict, str, list[str]]:
    warnings: list[str] = []
    anchor_desc = ""
    if floor:
        try:
            anchor = anchor_for_floor(
                floor,
                entry_room_id=cfg["entry_room_id"],
                room_locations=cfg["room_locations"],
                yaml_initial_pose=mapdata.initial_pose,
            )
            anchor_desc = anchor["source"]
            return anchor, anchor_desc, warnings
        except ValueError as exc:
            warnings.append(str(exc))

    if mapdata.initial_pose:
        anchor = {
            "kind": "anchor",
            "id": "yaml_entry",
            "label": "initial_pose",
            "x": mapdata.initial_pose["x"],
            "y": mapdata.initial_pose["y"],
            "yaw": mapdata.initial_pose.get("yaw", 0.0),
            "source": "yaml:initial_pose",
        }
        warnings.append("单图且不在 FLOOR_MAPS：使用 yaml initial_pose 作锚点")
        return anchor, "yaml:initial_pose", warnings

    warnings.append("无楼层且无 initial_pose：使用可通行域中心作锚点")
    rows, cols = mapdata.navigable_mask.nonzero()
    if len(rows) == 0:
        raise ValueError("地图无可通行区域")
    from patrol_core import grid_to_world

    mid = len(rows) // 2
    wx, wy = grid_to_world(mapdata, int(cols[mid]), int(rows[mid]))
    anchor = {
        "kind": "anchor",
        "id": "auto",
        "label": "auto_center",
        "x": wx,
        "y": wy,
        "yaw": 0.0,
        "source": "auto:navigable_center",
    }
    return anchor, anchor["source"], warnings


def load_mark_context(
    yaml_path: Path,
    map_dir: Path,
    cfg: dict,
    *,
    floor: str | None,
    key: str,
) -> MapMarkContext:
    from patrol_core import prepare_map_with_anchor

    mapdata = load_map(yaml_path, map_dir=map_dir)
    anchor, _desc, _w = resolve_anchor_for_map(floor, cfg, mapdata)
    anchor_cell, _ax, _ay, _prep = prepare_map_with_anchor(mapdata, anchor)
    return MapMarkContext(
        key=key,
        floor=floor,
        yaml_path=yaml_path,
        mapdata=mapdata,
        anchor=anchor,
        anchor_cell=anchor_cell,
    )


def load_mark_contexts_for_options(opts: PatrolRunOptions, cfg: dict) -> list[MapMarkContext]:
    map_dir = opts.map_dir.expanduser().resolve()
    out: list[MapMarkContext] = []
    if opts.command == "building":
        for floor, yaml_name in list_building_floors(cfg):
            yaml_path = map_dir / yaml_name
            if not yaml_path.is_file():
                continue
            out.append(
                load_mark_context(yaml_path, map_dir, cfg, floor=floor, key=floor)
            )
    else:
        yaml_path = resolve_map_yaml(opts.single_map, map_dir)
        floor = floor_for_map_yaml(yaml_path.name, cfg["floor_maps"])
        key = floor or yaml_path.stem
        out.append(load_mark_context(yaml_path, map_dir, cfg, floor=floor, key=key))
    return out


def _process_one_map(
    yaml_path: Path,
    map_dir: Path,
    out_subdir: Path,
    *,
    floor: str | None,
    cfg: dict,
    coverage_mode: str,
    max_uncovered_ratio: float | None,
    sample_step: float,
    dot_color: tuple[int, int, int],
    file_prefix: str,
    generation: str,
    manual_cells: list[tuple[int, int]] | None,
    mark_key: str,
    log: LogFn | None,
    lines: list[str],
) -> tuple[dict, list[str], dict[str, str], Path, Path]:
    warnings: list[str] = []
    mapdata = load_map(yaml_path, map_dir=map_dir)
    anchor, anchor_desc, aw = resolve_anchor_for_map(floor, cfg, mapdata)
    warnings.extend(aw)

    if generation == "manual":
        cells = manual_cells or []
        if not cells:
            warnings.append(f"[{mark_key}] 无转圈点：仅输出锚点路线")
        plan, plan_warnings, cover_vis = plan_patrol_manual(
            mapdata,
            floor=floor,
            anchor=anchor,
            patrol_cells=cells,
            coverage_mode=coverage_mode,
        )
    else:
        plan, plan_warnings, cover_vis = plan_patrol_for_map(
            mapdata,
            floor=floor,
            anchor=anchor,
            coverage_mode=coverage_mode,
            sample_step_m=sample_step,
            max_uncovered_ratio=max_uncovered_ratio,
        )
    warnings.extend(plan_warnings)

    out_subdir.mkdir(parents=True, exist_ok=True)
    stem = yaml_path.stem
    if floor:
        json_name = f"patrol_{floor}.json"
    elif file_prefix:
        json_name = f"{file_prefix}{stem}.json"
    else:
        json_name = f"patrol_{stem}.json"

    json_path = out_subdir / json_name
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    overlay_name = f"overlay_{stem}_patrol_{'red' if dot_color == (220, 50, 50) else 'blue'}.png"
    overlay_path = out_subdir / overlay_name
    render_overlay(
        mapdata,
        plan,
        cover_vis,
        overlay_path,
        dot_color=dot_color,
        coverage_mode=coverage_mode,
    )

    hashes = {
        yaml_path.name: file_sha256(yaml_path),
        mapdata.pgm_path.name: file_sha256(mapdata.pgm_path),
    }
    anchor_map = {floor or stem: anchor_desc} if anchor_desc else {}

    if generation == "manual":
        spin_n = len(plan.get("waypoints", [])) - 1
        ref_cov = plan.get("reference_coverage_ratio", plan.get("coverage_ratio", 0))
        _log(
            log,
            f"[{floor or stem}] {yaml_path.name} (manual): {spin_n} 个转圈点, "
            f"参考可见覆盖 {ref_cov:.1%}, 路线 {plan['route_length_m']:.1f}m",
            lines,
        )
    else:
        _log(
            log,
            f"[{floor or stem}] {yaml_path.name} ({generation}): {len(plan['waypoints'])} 点, "
            f"覆盖 {plan['coverage_ratio']:.1%}, 路线 {plan['route_length_m']:.1f}m",
            lines,
        )
    _log(log, f"  JSON   -> {json_path}", lines)
    _log(log, f"  叠加图 -> {overlay_path}", lines)

    return plan, warnings, {**hashes, **anchor_map}, json_path, overlay_path


def run_patrol(opts: PatrolRunOptions, log: LogFn | None = None) -> PatrolRunResult:
    lines: list[str] = []
    map_dir = opts.map_dir.expanduser().resolve()
    out_dir = opts.out_dir.expanduser().resolve()

    if not map_dir.is_dir():
        return PatrolRunResult(ok=False, message=f"地图目录不存在: {map_dir}", log_lines=lines)

    try:
        switcher_path = resolve_switcher_path(opts.switcher or None)
        cfg = load_switcher_config(switcher_path)
    except (FileNotFoundError, ValueError) as exc:
        return PatrolRunResult(ok=False, message=str(exc), log_lines=lines)

    all_warnings: list[str] = list(validate_floor_maps(cfg["floor_maps"]))
    plans: list[dict] = []
    map_hashes: dict[str, str] = {}
    anchors: dict[str, str] = {}
    overlay_paths: list[Path] = []
    json_paths: list[Path] = []

    params = {
        "generation": opts.generation,
        "coverage_mode": opts.coverage_mode,
        "max_uncovered_ratio": opts.max_uncovered_ratio,
        "sample_step_m": opts.sample_step,
        "inflate_m": opts.inflate,
        "spin_360": True,
        "manual_point_keys": list(opts.manual_points.keys()) if opts.generation == "manual" else [],
    }

    _log(log, f"巡逻规划工具 v{TOOL_VERSION}", lines)
    _log(log, f"地图目录: {map_dir}", lines)
    _log(log, f"输出目录: {out_dir}", lines)
    _log(log, f"switcher: {switcher_path}", lines)
    _log(log, f"生成方式: {opts.generation}", lines)
    _log(log, f"覆盖模式: {opts.coverage_mode}", lines)
    _log(log, "", lines)

    manifest_path: Optional[Path] = None

    try:
        if opts.command == "building":
            out_sub = out_dir / "building"
            seen_yaml: set[str] = set()
            for floor in sorted(cfg["floor_maps"].keys()):
                yaml_name = cfg["floor_maps"][floor]
                if yaml_name in seen_yaml:
                    all_warnings.append(f"跳过重复地图 {yaml_name} (楼层 {floor})")
                    continue
                seen_yaml.add(yaml_name)
                yaml_path = map_dir / yaml_name
                if not yaml_path.is_file():
                    all_warnings.append(f"楼层 {floor} 地图缺失: {yaml_path}")
                    continue
                manual_cells = opts.manual_points.get(floor, []) if opts.generation == "manual" else None
                plan, w, meta, jp, op = _process_one_map(
                    yaml_path,
                    map_dir,
                    out_sub,
                    floor=floor,
                    cfg=cfg,
                    coverage_mode=opts.coverage_mode,
                    max_uncovered_ratio=opts.max_uncovered_ratio,
                    sample_step=opts.sample_step,
                    dot_color=(220, 50, 50),
                    file_prefix="",
                    generation=opts.generation,
                    manual_cells=manual_cells,
                    mark_key=floor,
                    log=log,
                    lines=lines,
                )
                plans.append(plan)
                all_warnings.extend(w)
                json_paths.append(jp)
                overlay_paths.append(op)
                for k, v in meta.items():
                    if k.endswith(".yaml") or k.endswith(".pgm"):
                        map_hashes[k] = v
                    else:
                        anchors[k] = v

            manifest_path = out_dir / "manifest.json"
            write_manifest(
                manifest_path,
                mode="building",
                switcher_path=cfg["switcher_path"],
                floor_maps=cfg["floor_maps"],
                map_dir=map_dir,
                params=params,
                anchors=anchors,
                map_hashes=map_hashes,
                plans=plans,
                warnings=all_warnings,
            )

        elif opts.command == "single":
            if not opts.single_map.strip():
                return PatrolRunResult(ok=False, message="单图模式请选择地图 yaml", log_lines=lines)
            yaml_path = resolve_map_yaml(opts.single_map, map_dir)
            floor = floor_for_map_yaml(yaml_path.name, cfg["floor_maps"])
            mark_key = floor or yaml_path.stem
            manual_cells = opts.manual_points.get(mark_key, []) if opts.generation == "manual" else None
            out_sub = out_dir / "single"
            plan, w, meta, jp, op = _process_one_map(
                yaml_path,
                map_dir,
                out_sub,
                floor=floor,
                cfg=cfg,
                coverage_mode=opts.coverage_mode,
                max_uncovered_ratio=opts.max_uncovered_ratio,
                sample_step=opts.sample_step,
                dot_color=(50, 120, 220),
                file_prefix="patrol_",
                generation=opts.generation,
                manual_cells=manual_cells,
                mark_key=mark_key,
                log=log,
                lines=lines,
            )
            plans.append(plan)
            all_warnings.extend(w)
            json_paths.append(jp)
            overlay_paths.append(op)
            map_hashes = {k: v for k, v in meta.items() if k.endswith((".yaml", ".pgm"))}
            anchors = {k: v for k, v in meta.items() if not k.endswith((".yaml", ".pgm"))}

            manifest_path = out_dir / "manifest.json"
            write_manifest(
                manifest_path,
                mode="single",
                switcher_path=cfg["switcher_path"],
                floor_maps=cfg["floor_maps"],
                map_dir=map_dir,
                params=params,
                anchors=anchors,
                map_hashes=map_hashes,
                plans=plans,
                warnings=all_warnings,
            )
        else:
            return PatrolRunResult(ok=False, message=f"未知模式: {opts.command}", log_lines=lines)

    except Exception as exc:
        return PatrolRunResult(ok=False, message=str(exc), log_lines=lines, warnings=all_warnings)

    if all_warnings:
        _log(log, "", lines)
        _log(log, "警告:", lines)
        for w in all_warnings:
            _log(log, f"  - {w}", lines)

    _log(log, "", lines)
    _log(log, f"完成。manifest -> {manifest_path}", lines)

    return PatrolRunResult(
        ok=True,
        message="生成完成",
        plans=plans,
        warnings=all_warnings,
        overlay_paths=overlay_paths,
        json_paths=json_paths,
        manifest_path=manifest_path,
        log_lines=lines,
    )
