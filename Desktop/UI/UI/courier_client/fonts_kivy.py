"""
Kivy 中文字体：优先 courier_client/assets/fonts，其次仓库根 assets/fonts。
"""
from __future__ import annotations

from pathlib import Path

from kivy.core.text import LabelBase

FONT_FILENAME = "NotoSansCJKsc-Regular.otf"
FONT_NAME = "CourierCJK"


def _candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    root = here.parent
    return [
        here / "assets" / "fonts" / FONT_FILENAME,
        root / "assets" / "fonts" / FONT_FILENAME,
    ]


def register_chinese_font() -> str:
    for p in _candidates():
        if p.is_file() and p.stat().st_size > 50_000:
            try:
                LabelBase.register(name=FONT_NAME, fn_regular=str(p))
            except Exception:
                continue
            return FONT_NAME
    return ""
