"""巡逻路径规划核心：地图加载、覆盖规划、TSP、可视化。"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

TOOL_VERSION = "0.1.0"
SPIN_ACTION = "spin_360"
RAY_STEP_DEG = 10.0
PLAN_DOWNSAMPLE = 4
MAX_CANDIDATES = 800
MAX_PATROL_POINTS = 45


@dataclass
class MapData:
    yaml_path: Path
    pgm_path: Path
    pixels: np.ndarray
    resolution: float
    origin: tuple[float, float, float]
    free_thresh: float
    occupied_thresh: float
    negate: int
    mode: str
    free_mask: np.ndarray
    occupied_mask: np.ndarray
    unknown_mask: np.ndarray
    navigable_mask: np.ndarray
    reachable_mask: np.ndarray
    corridor_mask: np.ndarray
    room_mask: np.ndarray
    initial_pose: Optional[dict[str, float]] = None

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_pgm(path: Path) -> np.ndarray:
    with path.open("rb") as f:
        tokens: list[bytes] = []
        while len(tokens) < 3:
            line = f.readline()
            if not line or line.startswith(b"#"):
                continue
            tokens.extend(line.split())
        if len(tokens) < 3:
            raise ValueError(f"PGM 头无效: {path}")
        width, height = int(tokens[1]), int(tokens[2])
        if len(tokens) < 4:
            maxval_line = f.readline()
            while maxval_line.startswith(b"#"):
                maxval_line = f.readline()
            tokens.append(maxval_line.split()[0])
        maxval = int(tokens[3])
        raw = f.read()
    data = np.frombuffer(raw, dtype=np.uint8)
    if data.size > width * height:
        data = data[: width * height]
    elif data.size < width * height:
        raise ValueError(f"PGM 数据长度不足: {path}")
    img = data.reshape((height, width))
    if maxval != 255:
        img = (img.astype(np.float32) * (255.0 / maxval)).astype(np.uint8)
    return img


def _occupancy(pixel: np.ndarray, negate: int) -> np.ndarray:
    p = pixel.astype(np.float32)
    if negate:
        p = 255.0 - p
    return (255.0 - p) / 255.0


def _parse_occupancy_grid(
    pixels: np.ndarray, meta: dict[str, Any]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按 Nav2 语义解析栅格：free / occupied / unknown（三值地图 unknown 不可通行）。"""
    occ = _occupancy(pixels, int(meta.get("negate", 0)))
    free_t = float(meta.get("free_thresh", 0.25))
    occ_t = float(meta.get("occupied_thresh", 0.65))
    mode = str(meta.get("mode", "trinary"))
    free = occ < free_t
    occupied = occ > occ_t
    if mode == "trinary":
        unknown = ~free & ~occupied
    else:
        unknown = np.zeros_like(free, dtype=bool)
    return free, occupied, unknown


def _connected_component(mask: np.ndarray, col: int, row: int) -> np.ndarray:
    """从 (col,row) 四连通泛洪，得到与起点连通的 True 区域。"""
    from collections import deque

    h, w = mask.shape
    out = np.zeros_like(mask, dtype=bool)
    if not (0 <= col < w and 0 <= row < h) or not mask[row, col]:
        return out
    out[row, col] = True
    q: deque[tuple[int, int]] = deque([(col, row)])
    while q:
        c, r = q.popleft()
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < w and 0 <= nr < h and mask[nr, nc] and not out[nr, nc]:
                out[nr, nc] = True
                q.append((nc, nr))
    return out


def _largest_component(mask: np.ndarray) -> np.ndarray:
    from collections import deque

    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    best = np.zeros_like(mask, dtype=bool)
    best_n = 0
    for sr in range(h):
        for sc in range(w):
            if not mask[sr, sc] or visited[sr, sc]:
                continue
            comp = np.zeros_like(mask, dtype=bool)
            comp[sr, sc] = True
            visited[sr, sc] = True
            q: deque[tuple[int, int]] = deque([(sc, sr)])
            n = 1
            while q:
                c, r = q.popleft()
                for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nc, nr = c + dc, r + dr
                    if 0 <= nc < w and 0 <= nr < h and mask[nr, nc] and not visited[nr, nc]:
                        visited[nr, nc] = True
                        comp[nr, nc] = True
                        n += 1
                        q.append((nc, nr))
            if n > best_n:
                best_n = n
                best = comp
    return best


def restrict_to_reachable(mapdata: MapData, col: int, row: int) -> tuple[int, list[str]]:
    """仅保留与锚点连通的实际可活动区域（封闭主连通域）。"""
    warnings: list[str] = []
    comp = _connected_component(mapdata.navigable_mask, col, row)
    if int(comp.sum()) == 0:
        comp = _largest_component(mapdata.navigable_mask)
        warnings.append("锚点不在可通行区，已改用最大连通域作为活动范围")
    elif int(comp.sum()) < int(mapdata.navigable_mask.sum()):
        dropped = int(mapdata.navigable_mask.sum()) - int(comp.sum())
        warnings.append(
            f"已剔除与锚点不连通的游离可通行格 {dropped} 个（仅保留主封闭区域）"
        )

    mapdata.reachable_mask = comp
    mapdata.navigable_mask = comp.copy()
    mapdata.corridor_mask &= comp
    mapdata.room_mask &= comp
    return int(comp.sum()), warnings


def _distance_to_blocked(blocked: np.ndarray) -> np.ndarray:
    """近似距离变换：到最近障碍格的栅格距离。"""
    h, w = blocked.shape
    inf = h + w
    dist = np.full((h, w), inf, dtype=np.int32)
    dist[~blocked] = 0
    # 两遍扫描 chamfer
    for r in range(h):
        for c in range(w):
            d = dist[r, c]
            if r > 0:
                d = min(d, dist[r - 1, c] + 1)
            if c > 0:
                d = min(d, dist[r, c - 1] + 1)
            dist[r, c] = d
    for r in range(h - 1, -1, -1):
        for c in range(w - 1, -1, -1):
            d = dist[r, c]
            if r + 1 < h:
                d = min(d, dist[r + 1, c] + 1)
            if c + 1 < w:
                d = min(d, dist[r, c + 1] + 1)
            dist[r, c] = d
    return dist


def _inflate_blocked(blocked: np.ndarray, radius_cells: int) -> np.ndarray:
    if radius_cells <= 0:
        return blocked.copy()
    from numpy.lib.stride_tricks import sliding_window_view

    pad = radius_cells
    padded = np.pad(blocked.astype(np.uint8), pad, mode="constant", constant_values=1)
    h, w = blocked.shape
    windows = sliding_window_view(padded, (2 * radius_cells + 1, 2 * radius_cells + 1))
    return windows.max(axis=(2, 3)).astype(bool)


def load_map(yaml_path: Path, map_dir: Optional[Path] = None) -> MapData:
    yaml_path = yaml_path.resolve()
    meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    image_field = meta.get("image", "")
    pgm_path = Path(image_field)
    if not pgm_path.is_file():
        pgm_path = yaml_path.parent / Path(image_field).name
    if not pgm_path.is_file() and map_dir:
        pgm_path = map_dir / Path(image_field).name
    if not pgm_path.is_file():
        raise FileNotFoundError(f"找不到 PGM: {image_field} (yaml={yaml_path})")

    pixels = load_pgm(pgm_path)
    origin = tuple(float(x) for x in meta.get("origin", [0, 0, 0]))
    resolution = float(meta.get("resolution", 0.05))
    free_mask, occupied_mask, unknown_mask = _parse_occupancy_grid(pixels, meta)

    ip = meta.get("initial_pose")
    initial_pose = None
    if isinstance(ip, dict):
        initial_pose = {k: float(ip[k]) for k in ("x", "y", "yaw") if k in ip}
        if "z" in ip:
            initial_pose["z"] = float(ip["z"])

    inflate_cells = max(1, int(round(0.3 / resolution)))
    # 障碍 = 占用 + 未知（PGM 205）；仅 occ<free_thresh 的格才是可通行自由空间
    blocked = occupied_mask | unknown_mask
    inflated = _inflate_blocked(blocked, inflate_cells)
    navigable = free_mask & ~inflated

    dist = _distance_to_blocked(~navigable)
    corridor_radius = max(1, int(round(1.0 / resolution)))
    corridor_mask = navigable & (dist <= corridor_radius)
    room_mask = navigable & (dist > corridor_radius)
    reachable_mask = navigable.copy()

    return MapData(
        yaml_path=yaml_path,
        pgm_path=pgm_path.resolve(),
        pixels=pixels,
        resolution=resolution,
        origin=origin,
        free_thresh=float(meta.get("free_thresh", 0.25)),
        occupied_thresh=float(meta.get("occupied_thresh", 0.65)),
        negate=int(meta.get("negate", 0)),
        mode=str(meta.get("mode", "trinary")),
        initial_pose=initial_pose,
        free_mask=free_mask,
        occupied_mask=occupied_mask,
        unknown_mask=unknown_mask,
        navigable_mask=navigable,
        reachable_mask=reachable_mask,
        corridor_mask=corridor_mask,
        room_mask=room_mask,
    )


def world_to_grid(mapdata: MapData, x: float, y: float) -> tuple[int, int]:
    col = int((x - mapdata.origin[0]) / mapdata.resolution)
    row = mapdata.height - 1 - int((y - mapdata.origin[1]) / mapdata.resolution)
    return col, row


def grid_to_world(mapdata: MapData, col: int, row: int) -> tuple[float, float]:
    x = mapdata.origin[0] + (col + 0.5) * mapdata.resolution
    y = mapdata.origin[1] + (mapdata.height - 1 - row + 0.5) * mapdata.resolution
    return x, y


def _in_bounds(mapdata: MapData, col: int, row: int) -> bool:
    return 0 <= col < mapdata.width and 0 <= row < mapdata.height


def _is_blocked(mapdata: MapData, col: int, row: int) -> bool:
    if not _in_bounds(mapdata, col, row):
        return True
    # 射线：占用、未知、不可通行均遮挡
    if mapdata.occupied_mask[row, col] or mapdata.unknown_mask[row, col]:
        return True
    return not bool(mapdata.navigable_mask[row, col])


def _bresenham(col0: int, row0: int, col1: int, row1: int) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    dc = abs(col1 - col0)
    dr = abs(row1 - row0)
    sc = 1 if col0 < col1 else -1
    sr = 1 if row0 < row1 else -1
    err = dc - dr
    c, r = col0, row0
    while True:
        cells.append((c, r))
        if c == col1 and r == row1:
            break
        e2 = 2 * err
        if e2 > -dr:
            err -= dr
            c += sc
        if e2 < dc:
            err += dc
            r += sr
    return cells


def _snap_to_navigable(mapdata: MapData, x: float, y: float, search_radius: int = 40) -> tuple[int, int, float, float]:
    col, row = world_to_grid(mapdata, x, y)
    if _in_bounds(mapdata, col, row) and mapdata.navigable_mask[row, col]:
        wx, wy = grid_to_world(mapdata, col, row)
        return col, row, wx, wy
    best = None
    best_d = search_radius + 1
    for dr in range(-search_radius, search_radius + 1):
        for dc in range(-search_radius, search_radius + 1):
            nc, nr = col + dc, row + dr
            if not _in_bounds(mapdata, nc, nr):
                continue
            if not mapdata.navigable_mask[nr, nc]:
                continue
            d = abs(dc) + abs(dr)
            if d < best_d:
                best_d = d
                best = (nc, nr)
    if best is None:
        raise ValueError(f"锚点 ({x:.2f},{y:.2f}) 附近无可用导航格")
    nc, nr = best
    wx, wy = grid_to_world(mapdata, nc, nr)
    return nc, nr, wx, wy


def _sample_candidates_planning(
    pg: PlanningGrid,
    mapdata: MapData,
    sample_step_m: float,
) -> tuple[list[tuple[int, int]], list[str]]:
    eff_res = mapdata.resolution * pg.factor
    step = max(1, int(round(sample_step_m / eff_res)))
    warnings: list[str] = []

    def _collect(st: int) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for r in range(0, pg.height, st):
            for c in range(0, pg.width, st):
                if pg.navigable[r, c]:
                    out.append((c, r))
        return out

    candidates = _collect(step)
    while len(candidates) > MAX_CANDIDATES and step < pg.width:
        step += 1
        candidates = _collect(step)
    if step > max(1, int(round(sample_step_m / eff_res))):
        warnings.append(
            f"规划栅格候选点过多，步长由 {sample_step_m}m 放宽到约 {step * eff_res:.2f}m（{len(candidates)} 点）"
        )
    return candidates, warnings


def _target_mask(mapdata: MapData, coverage_mode: str) -> tuple[np.ndarray, np.ndarray]:
    """返回 (must_cover, optional_cover)。"""
    nav = mapdata.navigable_mask
    if coverage_mode == "corridor_priority":
        must = mapdata.corridor_mask.copy()
        optional = mapdata.room_mask.copy()
        return must & nav, optional & nav
    return nav.copy(), np.zeros_like(nav)


@dataclass
class PlanningGrid:
    """降采样栅格，用于加速覆盖规划。"""

    factor: int
    navigable: np.ndarray
    must_cover: np.ndarray
    optional_cover: np.ndarray

    @classmethod
    def from_mapdata(cls, mapdata: MapData, coverage_mode: str, factor: int = PLAN_DOWNSAMPLE) -> PlanningGrid:
        nav = mapdata.navigable_mask
        must_full, opt_full = _target_mask(mapdata, coverage_mode)
        fh = (nav.shape[0] // factor) * factor
        fw = (nav.shape[1] // factor) * factor
        block = lambda m: m[:fh, :fw].reshape(fh // factor, factor, fw // factor, factor).any(axis=(1, 3))
        nav_c = block(nav)
        must_c = block(must_full) & nav_c
        opt_c = block(opt_full) & nav_c
        return cls(factor=factor, navigable=nav_c, must_cover=must_c, optional_cover=opt_c)

    @property
    def height(self) -> int:
        return int(self.navigable.shape[0])

    @property
    def width(self) -> int:
        return int(self.navigable.shape[1])

    def full_to_plan(self, col: int, row: int) -> tuple[int, int]:
        return col // self.factor, row // self.factor

    def plan_to_full_center(self, pc: int, pr: int, mapdata: MapData) -> tuple[int, int]:
        col = pc * self.factor + self.factor // 2
        row = pr * self.factor + self.factor // 2
        col = min(max(0, col), mapdata.width - 1)
        row = min(max(0, row), mapdata.height - 1)
        if mapdata.navigable_mask[row, col]:
            return col, row
        for radius in range(1, self.factor * 2):
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    nc, nr = col + dc, row + dr
                    if _in_bounds(mapdata, nc, nr) and mapdata.navigable_mask[nr, nc]:
                        return nc, nr
        return col, row


def _pg_blocked(pg: PlanningGrid, col: int, row: int) -> bool:
    if not (0 <= col < pg.width and 0 <= row < pg.height):
        return True
    return not bool(pg.navigable[row, col])


def visibility_mask_planning(pg: PlanningGrid, col: int, row: int) -> np.ndarray:
    vis = np.zeros((pg.height, pg.width), dtype=bool)
    if not (0 <= col < pg.width and 0 <= row < pg.height):
        return vis
    limit = max(pg.height, pg.width) * 2
    for deg in np.arange(0.0, 360.0, RAY_STEP_DEG):
        rad = math.radians(float(deg))
        dc = math.cos(rad)
        dr = -math.sin(rad)
        c, r = float(col), float(row)
        for _ in range(limit):
            ci, ri = int(round(c)), int(round(r))
            if not (0 <= ci < pg.width and 0 <= ri < pg.height):
                break
            if _pg_blocked(pg, ci, ri) and not (ci == col and ri == row):
                break
            vis[ri, ci] = True
            c += dc
            r += dr
    return vis


def _upsample_vis_to_full(pg: PlanningGrid, vis_plan: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    fh, fw = vis_plan.shape
    full = np.zeros(shape, dtype=bool)
    f = pg.factor
    for pr in range(fh):
        for pc in range(fw):
            if not vis_plan[pr, pc]:
                continue
            r0, r1 = pr * f, min((pr + 1) * f, shape[0])
            c0, c1 = pc * f, min((pc + 1) * f, shape[1])
            full[r0:r1, c0:c1] = True
    return full


def visibility_mask(
    mapdata: MapData,
    col: int,
    row: int,
) -> np.ndarray:
    """全分辨率可见性（仅用于已选少量巡逻点的最终热力图）。"""
    vis = np.zeros((mapdata.height, mapdata.width), dtype=bool)
    if not _in_bounds(mapdata, col, row):
        return vis
    limit = max(mapdata.height, mapdata.width) * 2
    for deg in np.arange(0.0, 360.0, RAY_STEP_DEG):
        rad = math.radians(float(deg))
        dc = math.cos(rad)
        dr = -math.sin(rad)
        c, r = float(col), float(row)
        for _ in range(limit):
            ci, ri = int(round(c)), int(round(r))
            if not _in_bounds(mapdata, ci, ri):
                break
            if _is_blocked(mapdata, ci, ri) and not (ci == col and ri == row):
                break
            vis[ri, ci] = True
            c += dc
            r += dr
    return vis


def greedy_set_cover(
    mapdata: MapData,
    pg: PlanningGrid,
    candidates: list[tuple[int, int]],
    coverage_mode: str,
    max_uncovered_ratio: Optional[float],
    anchor_cell: tuple[int, int],
) -> tuple[list[tuple[int, int]], np.ndarray, dict[str, Any]]:
    must_full, optional_full = _target_mask(mapdata, coverage_mode)
    total_must = int(must_full.sum())
    total_optional = int(optional_full.sum())

    anchor_plan = pg.full_to_plan(anchor_cell[0], anchor_cell[1])
    selected_plan: list[tuple[int, int]] = []
    vis_cache: dict[tuple[int, int], np.ndarray] = {}

    def get_vis_full(cell_plan: tuple[int, int]) -> np.ndarray:
        if cell_plan not in vis_cache:
            vis_plan = visibility_mask_planning(pg, cell_plan[0], cell_plan[1])
            vis_cache[cell_plan] = _upsample_vis_to_full(pg, vis_plan, must_full.shape)
        return vis_cache[cell_plan]

    combined_vis = visibility_mask(mapdata, anchor_cell[0], anchor_cell[1])
    uncovered_must = must_full & ~combined_vis
    uncovered_optional = optional_full & ~combined_vis

    max_ratio = max_uncovered_ratio if max_uncovered_ratio is not None else 0.0

    def _full_stats() -> dict[str, float]:
        must_unc = int(uncovered_must.sum())
        opt_unc = int(uncovered_optional.sum()) if total_optional else 0
        return {
            "must_coverage_ratio": 1.0 - (must_unc / total_must) if total_must else 1.0,
            "optional_coverage_ratio": 1.0 - (opt_unc / total_optional) if total_optional else 1.0,
            "must_uncovered_cells": must_unc,
            "optional_uncovered_cells": opt_unc,
            "total_must_cells": total_must,
            "total_optional_cells": total_optional,
        }

    while len(selected_plan) < MAX_PATROL_POINTS:
        stats = _full_stats()
        if stats["must_coverage_ratio"] >= 1.0 - max_ratio:
            if coverage_mode == "full_free" or stats["optional_uncovered_cells"] <= int(
                total_optional * max_ratio
            ):
                break

        best_cell: Optional[tuple[int, int]] = None
        best_score = -1
        for cell in candidates:
            if cell in selected_plan or cell == anchor_plan:
                continue
            vis = get_vis_full(cell)
            gain_must = int((vis & uncovered_must).sum())
            gain_opt = int((vis & uncovered_optional).sum())
            score = gain_must * 1000 + gain_opt
            if score > best_score:
                best_score = score
                best_cell = cell

        if best_cell is None or best_score <= 0:
            break

        selected_plan.append(best_cell)
        vis = get_vis_full(best_cell)
        combined_vis |= vis
        uncovered_must &= ~vis
        uncovered_optional &= ~vis

    selected_full: list[tuple[int, int]] = []
    for pc, pr in selected_plan:
        selected_full.append(pg.plan_to_full_center(pc, pr, mapdata))

    stats = _full_stats()
    stats["patrol_point_count"] = len(selected_full)
    stats["planning_downsample"] = pg.factor
    if len(selected_plan) >= MAX_PATROL_POINTS and stats["must_coverage_ratio"] < 1.0 - max_ratio:
        stats["stopped_reason"] = "max_patrol_points"
    return selected_full, combined_vis, stats


def _tsp_nearest_neighbor(
    points: list[tuple[float, float]],
    start_idx: int = 0,
) -> list[int]:
    n = len(points)
    if n <= 1:
        return [0]
    visited = {start_idx}
    order = [start_idx]
    cur = start_idx
    while len(visited) < n:
        best_j = -1
        best_d = float("inf")
        cx, cy = points[cur]
        for j in range(n):
            if j in visited:
                continue
            px, py = points[j]
            d = math.hypot(px - cx, py - cy)
            if d < best_d:
                best_d = d
                best_j = j
        order.append(best_j)
        visited.add(best_j)
        cur = best_j
    return order


def _route_length(points: list[tuple[float, float]], order: list[int]) -> float:
    if len(order) < 2:
        return 0.0
    total = 0.0
    for i in range(len(order) - 1):
        a = points[order[i]]
        b = points[order[i + 1]]
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    # 闭环回起点
    a = points[order[-1]]
    b = points[order[0]]
    total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def _face_next_yaw(
    points: list[tuple[float, float]],
    order: list[int],
    idx_in_order: int,
    default_yaw: float = 0.0,
) -> float:
    if idx_in_order + 1 < len(order):
        cur = points[order[idx_in_order]]
        nxt = points[order[idx_in_order + 1]]
        return math.atan2(nxt[1] - cur[1], nxt[0] - cur[0])
    # 末点朝起点
    cur = points[order[idx_in_order]]
    start = points[order[0]]
    return math.atan2(start[1] - cur[1], start[0] - cur[0])


def plan_patrol_for_map(
    mapdata: MapData,
    *,
    floor: Optional[str],
    anchor: dict[str, Any],
    coverage_mode: str,
    sample_step_m: float,
    max_uncovered_ratio: Optional[float],
) -> tuple[dict[str, Any], list[str], np.ndarray]:
    warnings: list[str] = []
    ac, ar, ax, ay = _snap_to_navigable(mapdata, anchor["x"], anchor["y"])
    if abs(ax - anchor["x"]) > mapdata.resolution or abs(ay - anchor["y"]) > mapdata.resolution:
        warnings.append(
            f"锚点 ({anchor['x']:.2f},{anchor['y']:.2f}) 已吸附到最近可通行格 ({ax:.2f},{ay:.2f})"
        )

    reachable_cells, reach_warnings = restrict_to_reachable(mapdata, ac, ar)
    warnings.extend(reach_warnings)
    if reachable_cells == 0:
        raise ValueError("锚点连通域内无可活动区域")

    pg = PlanningGrid.from_mapdata(mapdata, coverage_mode)
    candidates, sample_warnings = _sample_candidates_planning(pg, mapdata, sample_step_m)
    warnings.extend(sample_warnings)
    if not candidates:
        raise ValueError("地图上无可用候选巡逻点")

    patrol_cells, cover_vis, cover_stats = greedy_set_cover(
        mapdata,
        pg,
        candidates,
        coverage_mode,
        max_uncovered_ratio,
        (ac, ar),
    )

    waypoints: list[dict[str, Any]] = []
    anchor_wp = {
        "index": 0,
        "kind": "anchor",
        "label": str(anchor.get("label", "anchor")),
        "id": str(anchor.get("id", "")),
        "x": round(ax, 4),
        "y": round(ay, 4),
        "yaw": round(float(anchor.get("yaw", 0.0)), 4),
        "grid_col": ac,
        "grid_row": ar,
        "source": anchor.get("source", ""),
    }
    waypoints.append(anchor_wp)

    for i, (pc, pr) in enumerate(patrol_cells, start=1):
        wx, wy = grid_to_world(mapdata, pc, pr)
        waypoints.append(
            {
                "index": i,
                "kind": "patrol",
                "label": f"P{i}",
                "id": f"P{i}",
                "x": round(wx, 4),
                "y": round(wy, 4),
                "yaw": 0.0,
                "grid_col": pc,
                "grid_row": pr,
            }
        )

    pts = [(wp["x"], wp["y"]) for wp in waypoints]
    visit_order = _tsp_nearest_neighbor(pts, start_idx=0)
    # 闭环：末尾回到 0
    route_order = visit_order + [0]

    for seq, wp_idx in enumerate(visit_order):
        wp = waypoints[wp_idx]
        if wp["kind"] == "patrol":
            wp["yaw"] = round(_face_next_yaw(pts, visit_order, seq), 4)
    if len(visit_order) > 1:
        waypoints[visit_order[-1]]["yaw"] = round(
            _face_next_yaw(pts, visit_order, len(visit_order) - 1), 4
        )

    route_len = _route_length(pts, visit_order)

    must_cov = cover_stats["must_coverage_ratio"]
    opt_cov = cover_stats["optional_coverage_ratio"]
    if coverage_mode == "full_free":
        coverage_ratio = must_cov
    else:
        coverage_ratio = must_cov * 0.7 + opt_cov * 0.3

    result = {
        "floor": floor or "",
        "map_yaml": mapdata.yaml_path.name,
        "map_pgm": mapdata.pgm_path.name,
        "generation": "auto",
        "coverage_mode": coverage_mode,
        "coverage_ratio": round(coverage_ratio, 4),
        "reachable_cells": reachable_cells,
        "reachable_area_m2": round(reachable_cells * mapdata.resolution ** 2, 2),
        "coverage_stats": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in cover_stats.items()},
        "anchor": anchor_wp,
        "waypoints": waypoints,
        "route_order": route_order,
        "route_length_m": round(route_len, 2),
        "generated_at": _now_iso(),
        "tool_version": TOOL_VERSION,
    }
    return result, warnings, cover_vis


def _coverage_stats_from_vis(
    mapdata: MapData,
    cover_vis: np.ndarray,
    coverage_mode: str,
) -> dict[str, Any]:
    must_full, optional_full = _target_mask(mapdata, coverage_mode)
    total_must = int(must_full.sum())
    total_optional = int(optional_full.sum())
    must_unc = int((must_full & ~cover_vis).sum())
    opt_unc = int((optional_full & ~cover_vis).sum()) if total_optional else 0
    return {
        "must_coverage_ratio": 1.0 - (must_unc / total_must) if total_must else 1.0,
        "optional_coverage_ratio": 1.0 - (opt_unc / total_optional) if total_optional else 1.0,
        "must_uncovered_cells": must_unc,
        "optional_uncovered_cells": opt_unc,
        "total_must_cells": total_must,
        "total_optional_cells": total_optional,
        "patrol_point_count": 0,
    }


def compute_cover_vis_from_cells(
    mapdata: MapData,
    anchor_cell: tuple[int, int],
    patrol_cells: list[tuple[int, int]],
) -> np.ndarray:
    cover = visibility_mask(mapdata, anchor_cell[0], anchor_cell[1])
    for c, r in patrol_cells:
        cover |= visibility_mask(mapdata, c, r)
    return cover


def build_plan_from_patrol_cells(
    mapdata: MapData,
    *,
    floor: Optional[str],
    anchor: dict[str, Any],
    anchor_cell: tuple[int, int],
    patrol_cells: list[tuple[int, int]],
    coverage_mode: str,
    cover_vis: np.ndarray,
    cover_stats: dict[str, Any],
    reachable_cells: int,
    generation: str = "auto",
) -> dict[str, Any]:
    ac, ar = anchor_cell
    ax, ay = grid_to_world(mapdata, ac, ar)

    waypoints: list[dict[str, Any]] = []
    anchor_wp = {
        "index": 0,
        "kind": "anchor",
        "label": str(anchor.get("label", "anchor")),
        "id": str(anchor.get("id", "")),
        "x": round(ax, 4),
        "y": round(ay, 4),
        "yaw": round(float(anchor.get("yaw", 0.0)), 4),
        "grid_col": ac,
        "grid_row": ar,
        "source": anchor.get("source", ""),
    }
    waypoints.append(anchor_wp)
    if generation == "manual":
        anchor_wp["action"] = "nav_only"
        anchor_wp["note"] = "路线起终点，不强制转圈（转圈仅在下方标记点执行）"

    for i, (pc, pr) in enumerate(patrol_cells, start=1):
        wx, wy = grid_to_world(mapdata, pc, pr)
        wp: dict[str, Any] = {
            "index": i,
            "kind": "patrol",
            "label": f"S{i}" if generation == "manual" else f"P{i}",
            "id": f"S{i}" if generation == "manual" else f"P{i}",
            "x": round(wx, 4),
            "y": round(wy, 4),
            "yaw": 0.0,
            "grid_col": pc,
            "grid_row": pr,
        }
        if generation == "manual":
            wp["action"] = SPIN_ACTION
            wp["note"] = "到站后原地360度转圈扫描"
        waypoints.append(wp)

    pts = [(wp["x"], wp["y"]) for wp in waypoints]
    visit_order = _tsp_nearest_neighbor(pts, start_idx=0)
    route_order = visit_order + [0]

    for seq, wp_idx in enumerate(visit_order):
        wp = waypoints[wp_idx]
        if wp["kind"] == "patrol":
            wp["yaw"] = round(_face_next_yaw(pts, visit_order, seq), 4)
    if len(visit_order) > 1:
        waypoints[visit_order[-1]]["yaw"] = round(
            _face_next_yaw(pts, visit_order, len(visit_order) - 1), 4
        )

    route_len = _route_length(pts, visit_order)
    must_cov = cover_stats["must_coverage_ratio"]
    opt_cov = cover_stats["optional_coverage_ratio"]
    coverage_ratio = must_cov if coverage_mode == "full_free" else must_cov * 0.7 + opt_cov * 0.3
    stats = dict(cover_stats)
    stats["patrol_point_count"] = len(patrol_cells)

    result: dict[str, Any] = {
        "floor": floor or "",
        "map_yaml": mapdata.yaml_path.name,
        "map_pgm": mapdata.pgm_path.name,
        "generation": generation,
        "coverage_mode": coverage_mode,
        "coverage_ratio": round(coverage_ratio, 4),
        "reachable_cells": reachable_cells,
        "reachable_area_m2": round(reachable_cells * mapdata.resolution ** 2, 2),
        "coverage_stats": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in stats.items()},
        "anchor": anchor_wp,
        "waypoints": waypoints,
        "route_order": route_order,
        "route_length_m": round(route_len, 2),
        "generated_at": _now_iso(),
        "tool_version": TOOL_VERSION,
    }
    if generation == "manual":
        result["purpose"] = "spin_360_at_marked_points"
        result["spin_stop_count"] = len(patrol_cells)
        result["reference_coverage_ratio"] = result["coverage_ratio"]
    return result


def prepare_map_with_anchor(
    mapdata: MapData,
    anchor: dict[str, Any],
) -> tuple[tuple[int, int], float, float, list[str]]:
    warnings: list[str] = []
    ac, ar, ax, ay = _snap_to_navigable(mapdata, anchor["x"], anchor["y"])
    if abs(ax - anchor["x"]) > mapdata.resolution or abs(ay - anchor["y"]) > mapdata.resolution:
        warnings.append(
            f"锚点 ({anchor['x']:.2f},{anchor['y']:.2f}) 已吸附到最近可通行格 ({ax:.2f},{ay:.2f})"
        )
    reachable_cells, reach_warnings = restrict_to_reachable(mapdata, ac, ar)
    warnings.extend(reach_warnings)
    if reachable_cells == 0:
        raise ValueError("锚点连通域内无可活动区域")
    return (ac, ar), ax, ay, warnings


def snap_patrol_click(mapdata: MapData, col: int, row: int, radius: int = 12) -> Optional[tuple[int, int]]:
    if _in_bounds(mapdata, col, row) and mapdata.reachable_mask[row, col]:
        return col, row
    best: Optional[tuple[int, int]] = None
    best_d = radius + 1
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            nc, nr = col + dc, row + dr
            if not _in_bounds(mapdata, nc, nr):
                continue
            if not mapdata.reachable_mask[nr, nc]:
                continue
            d = abs(dc) + abs(dr)
            if d < best_d:
                best_d = d
                best = (nc, nr)
    return best


def plan_patrol_manual(
    mapdata: MapData,
    *,
    floor: Optional[str],
    anchor: dict[str, Any],
    patrol_cells: list[tuple[int, int]],
    coverage_mode: str,
) -> tuple[dict[str, Any], list[str], np.ndarray]:
    warnings: list[str] = []
    anchor_cell, _ax, _ay, prep_warnings = prepare_map_with_anchor(mapdata, anchor)
    warnings.extend(prep_warnings)
    ac, ar = anchor_cell

    unique: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = {(ac, ar)}
    for c, r in patrol_cells:
        if (c, r) in seen:
            continue
        if not mapdata.reachable_mask[r, c]:
            warnings.append(f"忽略不可达手动点 ({c},{r})")
            continue
        seen.add((c, r))
        unique.append((c, r))

    cover_vis = compute_cover_vis_from_cells(mapdata, anchor_cell, unique)
    cover_stats = _coverage_stats_from_vis(mapdata, cover_vis, coverage_mode)
    reachable_cells = int(mapdata.reachable_mask.sum())

    if not unique:
        warnings.append("未标记转圈点：将只生成锚点路线（无额外停靠转圈）")

    plan = build_plan_from_patrol_cells(
        mapdata,
        floor=floor,
        anchor=anchor,
        anchor_cell=anchor_cell,
        patrol_cells=unique,
        coverage_mode=coverage_mode,
        cover_vis=cover_vis,
        cover_stats=cover_stats,
        reachable_cells=reachable_cells,
        generation="manual",
    )
    return plan, warnings, cover_vis


def compose_marking_preview(
    mapdata: MapData,
    anchor_cell: tuple[int, int],
    spin_cells: list[tuple[int, int]],
) -> Image.Image:
    """手动标记实时预览：绿区=已标记点（含锚点）360° 转圈可见范围。"""
    h, w = mapdata.height, mapdata.width
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[mapdata.unknown_mask] = (30, 40, 80, 140)
    nav = mapdata.reachable_mask
    rgba[nav] = (255, 255, 255, 18)
    cover = compute_cover_vis_from_cells(mapdata, anchor_cell, spin_cells)
    rgba[cover & nav] = (0, 200, 80, 100)
    overlay = Image.fromarray(rgba, mode="RGBA")
    base = Image.fromarray(mapdata.pixels).convert("RGBA")
    composed = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(composed)
    ac, ar = anchor_cell
    draw.polygon(
        [(ac, ar - 8), (ac - 7, ar + 6), (ac + 7, ar + 6)],
        fill=(0, 220, 80),
        outline=(255, 255, 255),
    )
    draw.text((ac + 10, ar - 10), "A", fill=(255, 255, 0))
    for i, (pc, pr) in enumerate(spin_cells, start=1):
        draw.ellipse((pc - 7, pr - 7, pc + 7, pr + 7), outline=(80, 160, 255), width=2)
        draw.ellipse((pc - 4, pr - 4, pc + 4, pr + 4), fill=(50, 120, 220), outline=(255, 255, 255))
        draw.text((pc + 10, pr - 10), f"S{i}", fill=(255, 255, 0))
    return composed.convert("RGB")


def render_overlay(
    mapdata: MapData,
    plan: dict[str, Any],
    cover_vis: np.ndarray,
    out_path: Path,
    *,
    dot_color: tuple[int, int, int],
    coverage_mode: str,
) -> None:
    is_manual = plan.get("generation") == "manual"
    must_cover, optional_cover = _target_mask(mapdata, coverage_mode)
    h, w = mapdata.height, mapdata.width

    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[mapdata.unknown_mask] = (30, 40, 80, 140)
    nav = mapdata.reachable_mask
    if is_manual:
        rgba[nav] = (255, 255, 255, 15)
        rgba[cover_vis & nav] = (0, 200, 80, 110)
    else:
        rgba[cover_vis & nav] = (0, 180, 60, 90)
        rgba[(~cover_vis) & must_cover & nav] = (200, 40, 40, 120)
        rgba[(~cover_vis) & optional_cover & nav] = (120, 120, 120, 80)
    overlay = Image.fromarray(rgba, mode="RGBA")

    base = Image.fromarray(mapdata.pixels).convert("RGB")
    composed = Image.alpha_composite(base.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(composed)

    wps = plan["waypoints"]
    order = plan["route_order"]
    pts_px = []
    for wp in wps:
        col, row = wp["grid_col"], wp["grid_row"]
        pts_px.append((col, row))

    for i in range(len(order) - 1):
        a = pts_px[order[i]]
        b = pts_px[order[i + 1]]
        draw.line([a, b], fill=(220, 220, 220), width=2)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    for seq, idx in enumerate(order[:-1]):
        wp = wps[idx]
        px, py = wp["grid_col"], wp["grid_row"]
        if wp["kind"] == "anchor":
            tri = [(px, py - 8), (px - 7, py + 6), (px + 7, py + 6)]
            draw.polygon(tri, fill=(0, 220, 80), outline=(255, 255, 255))
            label = "A"
        elif is_manual:
            draw.ellipse((px - 7, py - 7, px + 7, py + 7), outline=(80, 160, 255), width=2)
            draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill=dot_color, outline=(255, 255, 255))
            label = wp.get("label", str(seq))
        else:
            r = 6
            draw.ellipse((px - r, py - r, px + r, py + r), fill=dot_color, outline=(255, 255, 255))
            label = str(seq)
        draw.text((px + 8, py - 8), label, fill=(255, 255, 0), font=font)

    composed.convert("RGB").save(out_path)


def write_manifest(
    out_path: Path,
    *,
    mode: str,
    switcher_path: str,
    floor_maps: dict[str, str],
    map_dir: Path,
    params: dict[str, Any],
    anchors: dict[str, str],
    map_hashes: dict[str, str],
    plans: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    manifest = {
        "generated_at": _now_iso(),
        "tool_version": TOOL_VERSION,
        "mode": mode,
        "switcher_path": switcher_path,
        "map_dir": str(map_dir.resolve()),
        "floor_maps": floor_maps,
        "floor_order": sorted(floor_maps.keys()),
        "anchors": anchors,
        "params": params,
        "maps": map_hashes,
        "plans_summary": [
            {
                "floor": p.get("floor"),
                "map_yaml": p.get("map_yaml"),
                "coverage_ratio": p.get("coverage_ratio"),
                "waypoint_count": len(p.get("waypoints", [])),
                "route_length_m": p.get("route_length_m"),
            }
            for p in plans
        ],
        "warnings": warnings,
    }
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
