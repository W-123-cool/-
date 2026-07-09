"""NovaJoy 控制台组件：Card / StatusCard / MessageStream / ControlTopBar。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Tuple

from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from .brand import brand_asset_path
from .theme import C, apply_label_muted, apply_label_primary


def _bind_label(lb: Label, inset: float = 4) -> None:
    lb.bind(size=lambda w, *_: setattr(w, "text_size", (w.width - dp(inset), None)))


class NovaJoyCard(BoxLayout):
    """统一卡片：圆角、边框、内边距。"""

    def __init__(
        self,
        font_name: str = "",
        title: str = "",
        *,
        padding: float = 16,
        spacing: float = 12,
        **kwargs: Any,
    ) -> None:
        super().__init__(orientation="vertical", **kwargs)
        self.padding = (dp(padding),) * 4
        self.spacing = dp(spacing)
        self.size_hint_y = None
        self._fn = font_name

        with self.canvas.before:
            Color(*C.bg_card)
            self._fill = RoundedRectangle(
                pos=self.pos,
                size=self.size,
                radius=[dp(12), dp(12), dp(12), dp(12)],
            )
            Color(*C.border)
            self._border = Line(rounded_rectangle=(0, 0, 0, 0, dp(12)), width=dp(1))

        self.bind(pos=self._redraw, size=self._redraw)

        self.content = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        self.content.bind(minimum_height=self.content.setter("height"))

        if title:
            head = Label(
                text=title,
                font_size=dp(16),
                bold=True,
                size_hint_y=None,
                height=dp(28),
                halign="left",
                valign="middle",
            )
            if font_name:
                head.font_name = font_name
            head.color = C.accent_soft
            _bind_label(head)
            self.add_widget(head)

        self.add_widget(self.content)
        self.bind(minimum_height=self.setter("height"))

    def _redraw(self, *_args: Any) -> None:
        self._fill.pos = self.pos
        self._fill.size = self.size
        x, y = self.pos
        w, h = self.size
        self._border.rounded_rectangle = (x, y, w, h, dp(12))


class NovaJoyStatusCard(BoxLayout):
    """状态块：标题 + 数值 + 指示色点。"""

    def __init__(
        self,
        title: str,
        value: str = "—",
        font_name: str = "",
        *,
        indicator: Optional[Tuple[float, float, float, float]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(orientation="vertical", **kwargs)
        self.padding = (dp(10), dp(8))
        self.spacing = dp(4)
        self.size_hint = (1, None)
        self.height = dp(68)

        with self.canvas.before:
            Color(*C.bg_card)
            self._fill = RoundedRectangle(
                pos=self.pos,
                size=self.size,
                radius=[dp(10), dp(10), dp(10), dp(10)],
            )
            Color(*C.border)
            self._border = Line(rounded_rectangle=(0, 0, 0, 0, dp(10)), width=dp(1))
            self._dot_color = Color(*(indicator or C.accent))
            self._dot = RoundedRectangle(
                pos=(0, 0),
                size=(dp(8), dp(8)),
                radius=[dp(4), dp(4), dp(4), dp(4)],
            )

        self.bind(pos=self._redraw, size=self._redraw)

        cap = Label(
            text=title,
            font_size=dp(12),
            size_hint_y=None,
            height=dp(18),
            halign="left",
            valign="middle",
        )
        if font_name:
            cap.font_name = font_name
        apply_label_muted(cap)
        _bind_label(cap, 2)

        self.val = Label(
            text=value,
            font_size=dp(15),
            bold=True,
            size_hint_y=None,
            height=dp(24),
            halign="left",
            valign="middle",
        )
        if font_name:
            self.val.font_name = font_name
        apply_label_primary(self.val)
        _bind_label(self.val, 2)

        self.add_widget(cap)
        self.add_widget(self.val)

    def set_value(
        self,
        text: str,
        *,
        indicator: Optional[Tuple[float, float, float, float]] = None,
    ) -> None:
        self.val.text = text
        if indicator is not None:
            self._dot_color.rgba = indicator

    def _redraw(self, *_args: Any) -> None:
        self._fill.pos = self.pos
        self._fill.size = self.size
        x, y = self.pos
        w, h = self.size
        self._border.rounded_rectangle = (x, y, w, h, dp(10))
        self._dot.pos = (x + w - dp(16), y + h - dp(16))


class StatusStrip(GridLayout):
    """横向状态条（2~4 列）。"""

    def __init__(self, font_name: str = "", cols: int = 4, **kwargs: Any) -> None:
        super().__init__(cols=cols, spacing=dp(8), size_hint_y=None, height=dp(72), **kwargs)
        self._fn = font_name
        self._cards: dict[str, NovaJoyStatusCard] = {}

    def add_metric(self, key: str, title: str, value: str = "—") -> NovaJoyStatusCard:
        card = NovaJoyStatusCard(title, value, self._fn)
        self._cards[key] = card
        self.add_widget(card)
        return card

    def set_metric(
        self,
        key: str,
        value: str,
        *,
        indicator: Optional[Tuple[float, float, float, float]] = None,
    ) -> None:
        if key in self._cards:
            self._cards[key].set_value(value, indicator=indicator)


class MessageStream(BoxLayout):
    """底部消息流，替代大 TextInput 日志框。"""

    def __init__(
        self,
        font_name: str = "",
        title: str = "消息",
        *,
        height: float = 100,
        max_lines: int = 12,
        **kwargs: Any,
    ) -> None:
        super().__init__(orientation="vertical", size_hint_y=None, height=dp(height), **kwargs)
        self.padding = (dp(12), dp(8), dp(12), dp(10))
        self.spacing = dp(4)
        self._max = max_lines
        self._lines: list[str] = []
        self._fn = font_name

        with self.canvas.before:
            Color(*C.bg_secondary)
            self._bg = RoundedRectangle(
                pos=self.pos,
                size=self.size,
                radius=[dp(10), dp(10), dp(0), dp(0)],
            )
            Color(*C.accent)
            self._top_line = Line(points=[0, 0, 0, 0], width=dp(1))

        self.bind(pos=self._redraw, size=self._redraw)

        head = Label(
            text=title,
            font_size=dp(13),
            bold=True,
            size_hint_y=None,
            height=dp(20),
            halign="left",
        )
        if font_name:
            head.font_name = font_name
        head.color = C.accent_soft
        if title:
            self.add_widget(head)

        self._scroll = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        self._body = Label(
            text="—",
            font_size=dp(13),
            halign="left",
            valign="top",
            size_hint_y=None,
        )
        if font_name:
            self._body.font_name = font_name
        apply_label_muted(self._body)
        self._body.bind(texture_size=self._sync_body_height)
        self._scroll.add_widget(self._body)
        self.add_widget(self._scroll)

    def _sync_body_height(self, *_args: Any) -> None:
        self._body.height = max(self._body.texture_size[1], dp(20))

    def _redraw(self, *_args: Any) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size
        y_top = self.y + self.height
        self._top_line.points = [self.x, y_top, self.x + self.width, y_top]

    def _render(self) -> None:
        self._body.text = "\n".join(self._lines) if self._lines else "—"
        inner_w = max(self._scroll.width - dp(4), dp(80))
        self._body.text_size = (inner_w, None)

    @property
    def text(self) -> str:
        return self._body.text

    @text.setter
    def text(self, value: str) -> None:
        if not value or value == "—":
            self._lines = []
        else:
            self._lines = value.split("\n")
        self._render()

    def append(self, message: str, time_str: str = "") -> None:
        ts = time_str or datetime.now().strftime("%H:%M")
        self._lines.append(f"[{ts}] {message}")
        if len(self._lines) > self._max:
            self._lines = self._lines[-self._max :]
        self._render()

    def clear(self) -> None:
        self._lines = []
        self._render()


class ControlTopBar(BoxLayout):
    """控制台顶栏 72~90dp：Logo + 页面名 + 右侧状态芯片。"""

    def __init__(
        self,
        font_name: str = "",
        page_name: str = "",
        *,
        variant: Literal["icon", "logo"] = "logo",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = dp(80)
        self.padding = (dp(14), dp(10), dp(14), dp(10))
        self.spacing = dp(12)
        self._fn = font_name
        self._chips: dict[str, Label] = {}

        with self.canvas.before:
            Color(*C.bg_primary)
            self._bg = Rectangle(pos=self.pos, size=self.size)  # type: ignore[name-defined]
            Color(*C.accent)
            self._underline = Line(points=[0, 0, 0, 0], width=dp(1))

        self.bind(pos=self._redraw, size=self._redraw)

        asset = brand_asset_path(
            "novajoy_icon_72.png" if variant == "icon" else "novajoy_logo_header_sm.png"
        )
        logo_h = dp(48)
        self.add_widget(
            Image(
                source=asset,
                size_hint=(None, None),
                size=(logo_h if variant == "icon" else logo_h * 1.24, logo_h),
                allow_stretch=True,
                keep_ratio=True,
            )
        )

        mid = BoxLayout(orientation="vertical", spacing=dp(2))
        title = Label(
            text=page_name or "NovaJoy",
            font_size=dp(20),
            bold=True,
            size_hint_y=None,
            height=dp(26),
            halign="left",
            valign="middle",
        )
        if font_name:
            title.font_name = font_name
        apply_label_primary(title)
        title.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        sub = Label(
            text="Smart Building Robotics",
            font_size=dp(12),
            size_hint_y=None,
            height=dp(18),
            halign="left",
        )
        if font_name:
            sub.font_name = font_name
        apply_label_muted(sub)
        sub.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        mid.add_widget(title)
        mid.add_widget(sub)
        self.add_widget(mid)

        self._chip_row = BoxLayout(
            orientation="horizontal",
            spacing=dp(8),
            size_hint_x=None,
            width=dp(200),
        )
        self.add_widget(Widget(size_hint_x=1))
        self.add_widget(self._chip_row)

    def add_chip(self, key: str, text: str = "—") -> None:
        chip = Label(
            text=text,
            font_size=dp(12),
            size_hint=(None, None),
            size=(dp(88), dp(28)),
            halign="center",
            valign="middle",
        )
        if self._fn:
            chip.font_name = self._fn
        chip.color = C.text_primary
        with chip.canvas.before:
            Color(*C.bg_card)
            chip._bg = RoundedRectangle(  # type: ignore[attr-defined]
                pos=chip.pos,
                size=chip.size,
                radius=[dp(8), dp(8), dp(8), dp(8)],
            )
            Color(*C.border)
            chip._bd = Line(rounded_rectangle=(0, 0, 0, 0, dp(8)), width=dp(1))  # type: ignore[attr-defined]

        def _sync(inst: Label, *_a: Any) -> None:
            inst._bg.pos = inst.pos  # type: ignore[attr-defined]
            inst._bg.size = inst.size  # type: ignore[attr-defined]
            x, y = inst.pos
            w, h = inst.size
            inst._bd.rounded_rectangle = (x, y, w, h, dp(8))  # type: ignore[attr-defined]

        chip.bind(pos=_sync, size=_sync)
        chip.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        self._chips[key] = chip
        self._chip_row.add_widget(chip)
        self._chip_row.width = len(self._chips) * dp(96)

    def set_chip(self, key: str, text: str) -> None:
        if key in self._chips:
            self._chips[key].text = text

    def _redraw(self, *_args: Any) -> None:
        self._bg.pos = self.pos
        self._bg.size = self.size
        y = self.y
        self._underline.points = [self.x, y, self.x + self.width, y]
