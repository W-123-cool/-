#!/usr/bin/env python3
"""巡逻路径规划 CLI。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from patrol_core import TOOL_VERSION
from patrol_runner import PatrolRunOptions, run_patrol

_TOOL_ROOT = Path(__file__).resolve().parent
_DEFAULT_MAP_DIR = _TOOL_ROOT.parent.parent / "ros_ws" / "install" / "rt_robot_nav2" / "share" / "rt_robot_nav2" / "map"
_DEFAULT_OUT_DIR = _TOOL_ROOT.parent / "patrol_out"


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--map-dir", type=Path, default=_DEFAULT_MAP_DIR, help="地图目录 (pgm+yaml)")
    common.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR, help="输出目录")
    common.add_argument("--switcher", type=str, default="", help="switcher_node.py 路径")
    common.add_argument(
        "--coverage-mode",
        choices=("full_free", "corridor_priority"),
        default="full_free",
        help="覆盖模式: full_free=全部自由空间; corridor_priority=走廊优先",
    )
    common.add_argument(
        "--max-uncovered-ratio",
        type=float,
        default=None,
        help="允许未覆盖比例 (0~1)，默认 0 即尽量 100%%",
    )
    common.add_argument("--sample-step", type=float, default=0.25, help="候选点采样步长 (m)")
    common.add_argument("--inflate", type=float, default=0.3, help="障碍膨胀半径 (m)")

    p = argparse.ArgumentParser(
        description="NovaJoy 巡逻路径规划生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python generate_patrol.py building
  python generate_patrol.py single --map my_map3.yaml
  python patrol_ui.py
""",
    )

    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("building", parents=[common], help="全楼模式 (FLOOR_MAPS 登记的楼层)")
    sp_single = sub.add_parser("single", parents=[common], help="单图模式")
    sp_single.add_argument("--map", required=True, help="地图 yaml 文件名或路径")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    opts = PatrolRunOptions(
        command=args.command,
        map_dir=args.map_dir,
        out_dir=args.out_dir,
        switcher=args.switcher,
        coverage_mode=args.coverage_mode,
        max_uncovered_ratio=args.max_uncovered_ratio,
        sample_step=args.sample_step,
        inflate=args.inflate,
        single_map=getattr(args, "map", ""),
    )

    print(f"巡逻规划工具 v{TOOL_VERSION}")
    result = run_patrol(opts, log=print)
    if not result.ok:
        print(f"错误: {result.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
