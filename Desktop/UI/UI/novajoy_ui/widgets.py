"""NovaJoy styled Kivy widgets — layout/spacing/visual only."""
from __future__ import annotations

from typing import Any, Callable, Optional

from kivy.animation import Animation
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput

from .theme import C, apply_label_muted, apply_label_primary, style_button, style_popup, style_text_input


def _bind_halign(label: Label) -> None:
    label.bind(size=lambda w, *_: setattr(w, "text_size", (w.width - dp(8), None)))


def nvj_section_title(text: str, font_name: str) -> Label:
    lb = Label(
        text=text,
        size_hint_y=None,
        height=dp(36),
        font_size=dp(16),
        bold=True,
        halign="left",
        valign="middle",
    )
    if font_name:
        lb.font_name = font_name
    lb.color = C.accent_soft
    _bind_halign(lb)
    return lb


def nvj_field_label(text: str, font_name: str) -> Label:
    lb = Label(
        text=text,
        size_hint_y=None,
        height=dp(26),
        font_size=dp(13),
        halign="left",
        valign="middle",
    )
    if font_name:
        lb.font_name = font_name
    apply_label_muted(lb)
    _bind_halign(lb)
    return lb


def nvj_input(font_name: str, password: bool = False, **kw: Any) -> TextInput:
    ti = TextInput(
        multiline=False,
        size_hint_y=None,
        height=dp(46),
        font_size=dp(15),
        write_tab=False,
        **kw,
    )
    ti.password = password
    if font_name:
        ti.font_name = font_name
    style_text_input(ti)
    return ti


def nvj_button(
    text: str,
    font_name: str,
    on_press: Callable[[Any], None],
    *,
    height: Optional[float] = None,
    accent: bool = False,
    primary: bool = False,
    danger: bool = False,
) -> Button:
    is_primary = accent or primary
    b = Button(
        text=text,
        size_hint_y=None,
        height=height or dp(52),
        font_size=dp(15),
        background_normal="",
        background_down="",
    )
    if font_name:
        b.font_name = font_name
    style_button(b, enabled=True, accent=is_primary, primary=is_primary, danger=danger)

    def _press(instance: Button) -> None:
        if danger:
            down, up = C.btn_danger_down, C.btn_danger
        elif is_primary:
            down, up = C.btn_primary_down, C.btn_primary
        else:
            down, up = C.btn_neutral_down, C.btn_neutral
        Animation(background_color=down, d=0.12).start(instance)
        Animation(background_color=up, d=0.18, t="out_quad").start(instance)
        on_press(instance)

    b.bind(on_press=_press)
    return b


def show_nvj_popup(title: str, message: str, font_name: str) -> None:
    body = Label(
        text=message,
        text_size=(Window.width * 0.85 - dp(28), None),
        halign="left",
        valign="top",
        font_size=dp(14),
    )
    if font_name:
        body.font_name = font_name
    apply_label_primary(body)

    content = BoxLayout(
        orientation="vertical",
        padding=dp(14),
        spacing=dp(10),
    )
    content.add_widget(body)
    close = nvj_button("关闭", font_name, lambda *_: None, accent=True)
    pop = Popup(
        title=title,
        content=content,
        size_hint=(0.9, 0.55),
        title_font=font_name or "Roboto",
    )
    style_popup(pop)
    close.bind(on_press=pop.dismiss)
    content.add_widget(close)
    pop.open()
