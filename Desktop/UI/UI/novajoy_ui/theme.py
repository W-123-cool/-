"""NovaJoy Design System — color tokens and Kivy styling helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget


def _hex(h: str, alpha: float = 1.0) -> Tuple[float, float, float, float]:
    h = h.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return (r, g, b, alpha)


@dataclass(frozen=True)
class NovaJoyColors:
    bg_primary: Tuple[float, float, float, float] = _hex("081A2F")
    bg_secondary: Tuple[float, float, float, float] = _hex("102844")
    bg_card: Tuple[float, float, float, float] = _hex("122D4A")
    accent: Tuple[float, float, float, float] = _hex("00D4FF")
    accent_soft: Tuple[float, float, float, float] = _hex("40E0FF")
    success: Tuple[float, float, float, float] = _hex("00E676")
    warning: Tuple[float, float, float, float] = _hex("FFC400")
    danger: Tuple[float, float, float, float] = _hex("FF5252")
    text_primary: Tuple[float, float, float, float] = _hex("FFFFFF")
    text_secondary: Tuple[float, float, float, float] = _hex("A7C0D8")
    border: Tuple[float, float, float, float] = (0.0, 212 / 255, 1.0, 0.15)
    btn_neutral: Tuple[float, float, float, float] = _hex("203A5A")
    btn_neutral_down: Tuple[float, float, float, float] = _hex("1A3250")
    btn_disabled: Tuple[float, float, float, float] = _hex("1E2A3A", 0.65)
    btn_primary: Tuple[float, float, float, float] = _hex("00D4FF")
    btn_primary_down: Tuple[float, float, float, float] = _hex("00B8E0")
    btn_accent: Tuple[float, float, float, float] = _hex("00D4FF")
    btn_accent_down: Tuple[float, float, float, float] = _hex("00B8E0")
    btn_danger: Tuple[float, float, float, float] = _hex("FF5252")
    btn_danger_down: Tuple[float, float, float, float] = _hex("E04848")
    btn_tab_active: Tuple[float, float, float, float] = _hex("0D4A66")
    glow: Tuple[float, float, float, float] = (0.0, 212 / 255, 1.0, 0.22)


C = NovaJoyColors()


def apply_window_theme() -> None:
    """Global window background for all NovaJoy Kivy apps."""
    try:
        Window.clearcolor = C.bg_primary
    except Exception:
        pass


def apply_root_background(widget: Widget) -> None:
    """Paint root layout with primary background."""
    with widget.canvas.before:
        Color(*C.bg_primary)
        widget._nvj_bg = Rectangle(pos=widget.pos, size=widget.size)  # type: ignore[attr-defined]

    def _sync(inst: Widget, *_args: Any) -> None:
        inst._nvj_bg.pos = inst.pos  # type: ignore[attr-defined]
        inst._nvj_bg.size = inst.size  # type: ignore[attr-defined]

    widget.bind(pos=_sync, size=_sync)


def apply_label_primary(label: Label) -> None:
    label.color = C.text_primary


def apply_label_muted(label: Label) -> None:
    label.color = C.text_secondary


def style_text_input(ti: TextInput) -> None:
    ti.background_color = (1, 1, 1, 0.03)
    ti.foreground_color = C.text_primary
    ti.cursor_color = C.accent
    ti.hint_text_color = (*C.text_secondary[:3], 0.75)
    ti.padding = [dp(14), dp(12), dp(14), dp(12)]
    ti.multiline = ti.multiline  # keep existing


def style_readonly_log(ti: TextInput) -> None:
    style_text_input(ti)
    ti.background_color = (*C.bg_secondary[:3], 0.88)
    ti.foreground_color = C.text_secondary


def style_popup(pop: Popup) -> None:
    pop.background = ""
    pop.background_color = (*C.bg_card[:3], 0.96)
    pop.separator_color = C.border
    pop.title_color = C.text_primary


def style_button(
    btn: Any,
    *,
    enabled: bool = True,
    accent: bool = False,
    primary: bool = False,
    danger: bool = False,
    tab_active: bool = False,
) -> None:
    """primary=主 CTA(#00D4FF)；accent 同 primary；secondary=btn_neutral。"""
    if not enabled:
        btn.background_color = C.btn_disabled
        btn.color = (*C.text_secondary[:3], 0.55)
    elif tab_active:
        btn.background_color = C.btn_tab_active
        btn.color = C.text_primary
    elif danger:
        btn.background_color = C.btn_danger
        btn.color = C.text_primary
    elif accent or primary:
        btn.background_color = C.btn_primary
        btn.color = C.text_primary
    else:
        btn.background_color = C.btn_neutral
        btn.color = C.text_primary
    if hasattr(btn, "disabled_color"):
        btn.disabled_color = (*C.text_secondary[:3], 0.45)
