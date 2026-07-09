"""
Kivy 中文字体：Noto（assets）或系统字体回退；
注册为 Kivy default_font，避免漏设 font_name 时出现方框。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kivy.config import Config
from kivy.core.text import LabelBase

FONT_FILENAME = "NotoSansCJKsc-Regular.otf"
FONT_NAME = "PickupCJK"


def _candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    root = here.parent
    win = Path(os.environ.get("WINDIR", "C:\\Windows"))
    mobile = root / "user_client_mobile"
    return [
        here / "assets" / "fonts" / FONT_FILENAME,
        mobile / "assets" / "fonts" / FONT_FILENAME,
        root / "courier_client" / "assets" / "fonts" / FONT_FILENAME,
        root / "onboard_client" / "assets" / "fonts" / FONT_FILENAME,
        root / "assets" / "fonts" / FONT_FILENAME,
        win / "Fonts" / "msyh.ttc",
        win / "Fonts" / "simhei.ttf",
        win / "Fonts" / "simsun.ttc",
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]


def register_chinese_font() -> str:
    """注册字体、设为 default_font；成功返回 FONT_NAME，失败返回空串。"""
    for p in _candidates():
        if not p.is_file():
            continue
        try:
            if p.stat().st_size < 50_000 and p.suffix.lower() not in (".ttc", ".ttf", ".otf"):
                continue
            LabelBase.register(name=FONT_NAME, fn_regular=str(p))
            Config.set("kivy", "default_font", [FONT_NAME, "Roboto", "DejaVuSans"])
            return FONT_NAME
        except Exception:
            continue
    return ""


def apply_font(widget: Any, font_name: str) -> None:
    """为 Label / Button / TextInput / Popup 等设置 font_name。"""
    if not font_name:
        return
    if hasattr(widget, "font_name"):
        widget.font_name = font_name
    if hasattr(widget, "title_font"):
        widget.title_font = font_name


def apply_font_tree(widget: Any, font_name: str) -> None:
    """递归为子树中所有支持 font_name 的控件设置中文字体。"""
    if not font_name:
        return
    apply_font(widget, font_name)
    for child in getattr(widget, "children", []):
        apply_font_tree(child, font_name)
