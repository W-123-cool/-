"""
送货员端入口占位：正式 UI 已拆到 `courier_client`（Kivy）。

PC / arm64 Ubuntu：
    python -m courier_client.main
"""
from __future__ import annotations


def main() -> int:
    print("送货员端请运行: python -m courier_client.main")
    print("（与取货端 user_client 共用同一 FastAPI 后端）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
