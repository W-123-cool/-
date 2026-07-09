"""NovaJoy 移动端组件：紧凑顶栏 + 底部导航。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label

from .brand import brand_asset_path
from .theme import C, apply_label_muted, apply_label_primary, style_button


class MobileTopBar(BoxLayout):
    """手机顶栏 56~64dp：小 Logo + 标题 + 可选状态点。"""

    def __init__(
        self,
        font_name: str = "",
        title: str = "NovaJoy",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = dp(60)
        self.padding = (dp(16), dp(8), dp(16), dp(8))
        self.spacing = dp(10)
        self._fn = font_name

        with self.canvas.before:
            Color(*C.bg_primary)
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(*C.accent)
            self._line = Line(points=[0, 0, 0, 0], width=dp(1))

        self.bind(pos=self._sync_canvas, size=self._sync_canvas)

        logo_h = dp(36)
        self.add_widget(
            Image(
                source=brand_asset_path("novajoy_icon_72.png"),
                size_hint=(None, None),
                size=(logo_h, logo_h),
                allow_stretch=True,
                keep_ratio=True,
            )
        )

        col = BoxLayout(orientation="vertical", spacing=0)
        self.title_label = Label(
            text=title,
            font_size=dp(17),
            bold=True,
            halign="left",
            valign="middle",
            size_hint_y=None,
            height=dp(22),
        )
        if font_name:
            self.title_label.font_name = font_name
        apply_label_primary(self.title_label)
        self.title_label.bind(
            size=lambda w, *_: setattr(w, "text_size", (w.width, None))
        )
        sub = Label(
            text="Smart Building Robotics",
            font_size=dp(11),
            size_hint_y=None,
            height=dp(16),
            halign="left",
        )
        if font_name:
            sub.font_name = font_name
        apply_label_muted(sub)
        sub.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        col.add_widget(self.title_label)
        col.add_widget(sub)
        self.add_widget(col)

    def set_title(self, title: str) -> None:
        self.title_label.text = title

    def _sync_canvas(self, *_args: Any) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size
        y = self.y
        self._line.points = [self.x, y, self.x + self.width, y]


class BottomNavigation(BoxLayout):
    """底部 5 Tab 导航。"""

    TABS = (
        ("home", "首页"),
        ("tasks", "任务"),
        ("robot", "机器人"),
        ("messages", "消息"),
        ("profile", "我的"),
    )

    def __init__(
        self,
        font_name: str = "",
        on_select: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = dp(62)
        self.padding = (dp(4), dp(6))
        self.spacing = dp(2)
        self._fn = font_name
        self._on_select = on_select
        self._buttons: dict[str, Button] = {}
        self._current = "home"

        with self.canvas.before:
            Color(*C.bg_secondary)
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(*C.accent)
            self._top = Line(points=[0, 0, 0, 0], width=dp(1))

        self.bind(pos=self._sync_canvas, size=self._sync_canvas)

        for key, label in self.TABS:
            b = Button(
                text=label,
                font_size=dp(12),
                size_hint_x=1,
                background_normal="",
                background_down="",
            )
            if font_name:
                b.font_name = font_name
            style_button(b, enabled=True, tab_active=(key == self._current))
            b.bind(on_press=lambda _w, k=key: self.select(k))
            self._buttons[key] = b
            self.add_widget(b)

    def select(self, key: str) -> None:
        if key not in self._buttons:
            return
        self._current = key
        for k, b in self._buttons.items():
            style_button(b, enabled=True, tab_active=(k == key))
        if self._on_select:
            self._on_select(key)

    def _sync_canvas(self, *_args: Any) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size
        y_top = self.y + self.height
        self._top.points = [self.x, y_top, self.x + self.width, y_top]
