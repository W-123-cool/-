"""触控友好控件（车载横屏，NovaJoy 主题）。"""
from __future__ import annotations

from typing import Any, Callable, Optional

from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput

from novajoy_ui.theme import (
    C,
    apply_label_muted,
    apply_label_primary,
    style_button as _theme_style_button,
    style_popup,
    style_readonly_log,
    style_text_input,
)

from .fonts_kivy import apply_font


def style_button(
    btn: Button,
    *,
    enabled: bool = True,
    accent: bool = False,
    tab_active: bool = False,
) -> None:
    _theme_style_button(
        btn,
        enabled=enabled,
        accent=accent,
        tab_active=tab_active,
    )


def safe_text(value: Any) -> str:
    """Kivy Label/TextInput.text 仅接受 str。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def cn_label(text: Any, font_name: str, **kw: Any) -> Label:
    kw.pop("text", None)
    kw.pop("bold", None)
    lb = Label(text=safe_text(text), **kw)
    apply_font(lb, font_name)
    if "color" not in kw:
        apply_label_primary(lb)
    lb.bind(size=lambda w, *_: setattr(w, "text_size", (w.width - dp(4), None)))
    return lb


def touch_btn(
    text: str,
    font_name: str,
    on_press: Callable[[Any], None],
    *,
    height: Optional[float] = None,
    accent: bool = False,
) -> Button:
    b = Button(
        text=text,
        size_hint_y=None,
        height=height or dp(58),
        font_size=dp(18),
        background_normal="",
        background_down="",
    )
    apply_font(b, font_name)
    style_button(b, enabled=True, accent=accent)
    b.bind(on_press=on_press)
    return b


def section_label(text: str, font_name: str) -> Label:
    lb = cn_label(
        text,
        font_name,
        size_hint_y=None,
        height=dp(36),
        font_size=dp(16),
        halign="left",
        valign="middle",
        color=C.accent_soft,
    )
    return lb


def field_input(font_name: str, **kw: Any) -> TextInput:
    ti = TextInput(
        multiline=False,
        size_hint_y=None,
        height=dp(50),
        font_size=dp(18),
        write_tab=False,
        **kw,
    )
    apply_font(ti, font_name)
    style_text_input(ti)
    return ti


def style_log_input(ti: TextInput) -> None:
    style_readonly_log(ti)


def show_toast(title: str, message: Any, font_name: str) -> None:
    body = cn_label(
        safe_text(message),
        font_name,
        text_size=(Window.width * 0.82 - dp(24), None),
        halign="left",
        valign="top",
        font_size=dp(16),
    )
    apply_label_muted(body)
    box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
    box.add_widget(body)
    close = touch_btn("关闭", font_name, lambda *_: None, height=dp(52), accent=True)
    pop = Popup(
        title=safe_text(title),
        content=box,
        size_hint=(0.88, 0.5),
        title_font=font_name or "Roboto",
    )
    style_popup(pop)
    close.bind(on_press=pop.dismiss)
    box.add_widget(close)
    pop.open()
