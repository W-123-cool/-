"""NovaJoy brand header with logo assets."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from .theme import C, apply_label_muted, apply_label_primary


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def brand_asset_path(name: str) -> str:
    """Resolve branding PNG under assets/branding/."""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "assets" / "branding" / name,
        Path(__file__).resolve().parents[1] / "assets" / "branding" / name,
        here.parent / "onboard_client" / "assets" / "branding" / name,
        here.parent / "user_client" / "assets" / "branding" / name,
        here.parent / "courier_client" / "assets" / "branding" / name,
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    root = _repo_root()
    fallback = {
        "novajoy_logo_full.png": "有字图标.png",
        "novajoy_icon.png": "无字图标.png",
        "novajoy_logo_header.png": "有字图标.png",
        "novajoy_icon_96.png": "无字图标.png",
    }
    alt = root / fallback.get(name, name)
    return str(alt if alt.is_file() else candidates[0])


class BrandHeaderBar(BoxLayout):
    """
    Top brand strip: icon-only (landscape/onboard) or full logo (portrait apps).
    Pure presentation — no business logic.
    """

    def __init__(
        self,
        font_name: str = "",
        *,
        variant: Literal["icon", "logo"] = "logo",
        subtitle: str = "",
        product_line: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.padding = (dp(12), dp(8), dp(12), dp(6))
        self.spacing = dp(10)

        with self.canvas.before:
            Color(*C.bg_secondary)
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(*C.border)
            self._border = Line(points=[0, 0, 0, 0], width=dp(1))

        self.bind(pos=self._redraw, size=self._redraw)

        if variant == "icon":
            asset = brand_asset_path("novajoy_icon_96.png")
            logo_h = dp(52)
        else:
            asset = brand_asset_path("novajoy_logo_header.png")
            logo_h = dp(56)

        self.height = logo_h + dp(20) + (dp(18) if subtitle else 0)

        logo = Image(
            source=asset,
            size_hint=(None, None),
            size=(logo_h if variant == "icon" else logo_h * 1.24, logo_h),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.add_widget(logo)

        text_col = BoxLayout(orientation="vertical", spacing=dp(2))
        if variant == "icon":
            title = Label(
                text="NovaJoy",
                font_size=dp(20),
                bold=True,
                halign="left",
                valign="middle",
                size_hint_y=None,
                height=dp(26),
            )
            if font_name:
                title.font_name = font_name
            apply_label_primary(title)
            title.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
            text_col.add_widget(title)

            if product_line:
                pl = Label(
                    text=product_line,
                    font_size=dp(13),
                    halign="left",
                    valign="middle",
                    size_hint_y=None,
                    height=dp(20),
                )
                if font_name:
                    pl.font_name = font_name
                pl.color = C.accent_soft
                pl.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
                text_col.add_widget(pl)

        if subtitle:
            sub = Label(
                text=subtitle,
                font_size=dp(12),
                halign="left",
                valign="middle",
                size_hint_y=None,
                height=dp(18),
            )
            if font_name:
                sub.font_name = font_name
            apply_label_muted(sub)
            sub.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
            text_col.add_widget(sub)

        if variant == "icon" or subtitle:
            self.add_widget(text_col)

    def _redraw(self, *_args) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size
        y = self.y
        self._border.points = [
            self.x,
            y,
            self.x + self.width,
            y,
        ]
