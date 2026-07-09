"""
车载集成端：导览 + 送货，控制台布局（触控横屏）。

运行（项目根目录 送货业务本地测试v0.1）:
    python -m onboard_client.main

环境变量:
    ONBOARD_MODE=api     # 默认，送货/队列走后端；取货由 user_client 发起
    ONBOARD_MODE=local   # 仅练导览状态机（障碍仍模拟），不连后端
    ONBOARD_DEBUG_SPLIT=1  # 左右分栏调试（PC）
    COURIER_API_BASE=http://127.0.0.1:8000
    ONBOARD_DEFAULT_TAB=tour   # delivery | tour，导览联调默认 tour
"""
from __future__ import annotations

import os
from typing import Any, Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager

from novajoy_ui import (
    ControlTopBar,
    MessageStream,
    NovaJoyStatusCard,
    apply_root_background,
    apply_window_theme,
    C,
)

from .fonts_kivy import register_chinese_font
from .onboard_state import get_controller
from .panels import DeliveryPanel, TourPanel
from .widgets import cn_label, safe_text, show_toast, style_button, touch_btn


def _env_split() -> bool:
    return os.environ.get("ONBOARD_DEBUG_SPLIT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class NavRail(BoxLayout):
    """左侧垂直导航（替代横向 Tab）。"""

    def __init__(self, sm: ScreenManager, font_name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.size_hint_x = None
        self.width = dp(112)
        self.spacing = dp(10)
        self.padding = (dp(8), dp(10))
        self._sm = sm
        self._fn = font_name
        self._tabs: dict[str, Button] = {}

        for key, title in (("delivery", "送货"), ("tour", "导览")):
            b = touch_btn(
                title,
                font_name,
                lambda _w, k=key: self._select(k),
                height=dp(64),
            )
            b.font_size = dp(18)
            self._tabs[key] = b
            self.add_widget(b)
        default_tab = os.environ.get("ONBOARD_DEFAULT_TAB", "delivery").strip().lower()
        self._select("tour" if default_tab == "tour" else "delivery")

    def _select(self, name: str) -> None:
        self._sm.current = name
        for k, b in self._tabs.items():
            style_button(b, enabled=True, tab_active=(k == name))


class DeliveryScreen(Screen):
    def __init__(self, root: "OnboardRoot", font_name: str, **kwargs: Any) -> None:
        super().__init__(name="delivery", **kwargs)
        self.panel = DeliveryPanel(root, font_name)
        self.add_widget(self.panel)


class TourScreen(Screen):
    def __init__(self, root: "OnboardRoot", font_name: str, **kwargs: Any) -> None:
        super().__init__(name="tour", **kwargs)
        self.panel = TourPanel(root, font_name)
        self.add_widget(self.panel)


class SplitDebugLayout(BoxLayout):
    """PC 调试：左右分栏同时显示送货与导览。"""

    def __init__(self, root: "OnboardRoot", font_name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.spacing = dp(8)
        self.padding = dp(8)
        self.tour_panel = TourPanel(root, font_name)
        self.delivery_panel = DeliveryPanel(root, font_name)
        self.add_widget(self.tour_panel)
        self.add_widget(self.delivery_panel)


class OnboardRoot(BoxLayout):
    def __init__(self, app: "OnboardApp", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(6)
        self.padding = (dp(6), dp(6), dp(6), dp(0))
        self._app = app
        self._fn = app.cn_font
        self._ctrl = get_controller()
        self._ctrl.add_listener(self.refresh_chrome)
        apply_root_background(self)

        self.top_bar = ControlTopBar(
            self._fn,
            page_name="车载控制中心",
            variant="icon",
        )
        self.top_bar.add_chip("mode", "local" if self._ctrl.mode == "local" else "api")
        self.top_bar.add_chip("link", "—")
        self.add_widget(self.top_bar)

        self._voice_awake = False
        self._voice_recording = False
        self._voice_partial = ""
        voice_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(48),
            spacing=dp(10),
            padding=(dp(8), 0, dp(8), 0),
        )
        self._voice_btn = touch_btn("语音输入", self._fn, lambda *_: self._on_voice_tap())
        self._voice_btn.size_hint_x = 0.28
        style_button(self._voice_btn, enabled=True, accent=True)
        self._voice_hint = cn_label(
            "未唤醒 · 点一次唤醒，再点开始说话",
            self._fn,
            size_hint_x=0.72,
            font_size=dp(14),
            halign="left",
            color=C.text_secondary,
        )
        self._voice_hint.bind(
            size=lambda w, *_: setattr(w, "text_size", (w.width - dp(8), None))
        )
        voice_row.add_widget(self._voice_btn)
        voice_row.add_widget(self._voice_hint)
        self.add_widget(voice_row)
        if self._ctrl.mode != "api":
            self._voice_btn.disabled = True
            self._voice_hint.text = safe_text("本地模式无语音链路，请用 ONBOARD_MODE=api")
        self._sync_voice_chrome()

        if _env_split():
            self._split = SplitDebugLayout(self, self._fn, size_hint=(1, 1))
            self.add_widget(self._split)
            self._sm: Optional[ScreenManager] = None
            self._nav: Optional[NavRail] = None
            self._delivery_panel = self._split.delivery_panel
            self._status_tour = None
            self._status_delivery = None
            self._status_alert = None
        else:
            self._split = None
            body = BoxLayout(orientation="horizontal", size_hint=(1, 1), spacing=dp(8))

            self._sm = ScreenManager(transition=NoTransition())
            self._sm.add_widget(DeliveryScreen(self, self._fn))
            self._sm.add_widget(TourScreen(self, self._fn))

            self._nav = NavRail(self._sm, self._fn)
            body.add_widget(self._nav)
            self._sm.size_hint_x = 0.68
            body.add_widget(self._sm)

            status_col = BoxLayout(
                orientation="vertical",
                size_hint_x=0.32,
                spacing=dp(8),
                padding=(0, dp(4)),
            )
            self._status_tour = NovaJoyStatusCard("导览", "—", self._fn)
            self._status_delivery = NovaJoyStatusCard("送货", "—", self._fn)
            self._status_alert = NovaJoyStatusCard("互斥/告警", "正常", self._fn, indicator=C.success)
            status_col.add_widget(self._status_tour)
            status_col.add_widget(self._status_delivery)
            status_col.add_widget(self._status_alert)
            body.add_widget(status_col)

            self.add_widget(body)
            self._delivery_panel = self._sm.get_screen("delivery").panel  # type: ignore

        self._log_count = 0

        self.msg = MessageStream(self._fn, title="集成事件", height=96)
        mode = "本地" if self._ctrl.mode == "local" else f"API · {self._ctrl.api_base}"
        split = " · 分屏调试" if _env_split() else ""
        self.msg.append(f"模式: {mode}{split}")
        self.add_widget(self.msg)

    def append_log(self, text: str) -> None:
        self.msg.append(safe_text(text))

    def _on_voice_tap(self) -> None:
        ok, msg, action = self._ctrl.voice_ui_tap()
        if not ok:
            show_toast("语音", msg, self._fn)
            return
        if action == "wake":
            self._voice_awake = True
            self._voice_recording = False
        elif action == "begin":
            self._voice_awake = True
            self._voice_recording = True
            self._voice_partial = ""
        elif action == "end":
            self._voice_recording = False
        self.append_log(f"语音·{msg}")
        self._sync_voice_chrome()
        self.refresh_chrome()

    def _sync_voice_chrome(self) -> None:
        if self._voice_recording:
            self._voice_btn.text = "结束录音"
            hint = f"录音中… {self._voice_partial}" if self._voice_partial else "录音中… 请说话"
        elif self._voice_awake:
            self._voice_btn.text = "开始说话"
            hint = "已唤醒 · 点一下开始说话"
        else:
            self._voice_btn.text = "语音输入"
            hint = "未唤醒 · 点一下唤醒"
        self._voice_hint.text = safe_text(hint[:48])

    def _poll_voice_ptt(self) -> None:
        if self._ctrl.mode != "api":
            return
        try:
            st = self._ctrl.voice_ptt_status()
            awake = bool(st.get("awake"))
            rec = bool(st.get("recording"))
            partial = str(st.get("partial") or "")
            changed = (
                awake != self._voice_awake
                or rec != self._voice_recording
                or (partial and partial != self._voice_partial)
            )
            if partial and partial != self._voice_partial:
                self.append_log(f"识别: {partial}")
            self._voice_awake = awake
            self._voice_recording = rec
            self._voice_partial = partial
            if changed:
                self._sync_voice_chrome()
        except Exception:
            pass

    def refresh_chrome(self) -> None:
        c = self._ctrl
        tgt = safe_text(c.tour.target or "—")
        tour_txt = f"{c.tour.state_label} · {tgt}"
        if self._status_tour is not None:
            self._status_tour.set_value(tour_txt[:22], indicator=C.accent_soft)

        if c.mode == "local":
            del_txt = "未联后端"
        else:
            n = int(getattr(c, "pending_delivery_count", 0) or 0)
            del_txt = f"{c.delivery_robot_label()} · 待{n}单"
        if self._status_delivery is not None:
            self._status_delivery.set_value(del_txt[:22], indicator=C.accent)

        banner = safe_text(c.mutual_banner())
        if self._status_alert is not None:
            if banner:
                self._status_alert.set_value(banner[:20], indicator=C.warning)
            else:
                self._status_alert.set_value("正常", indicator=C.success)

        self.top_bar.set_chip("mode", c.mode)
        self.top_bar.set_chip("link", "运行中" if not banner else "互斥")

        logs = c.integration_logs()
        for entry in logs[self._log_count :]:
            self.msg.append(safe_text(entry))
        self._log_count = len(logs)

        if self._sm:
            tour_screen = self._sm.get_screen("tour")
            if hasattr(tour_screen, "panel"):
                tour_screen.panel._sync_buttons()  # type: ignore
        elif self._split:
            self._split.tour_panel._sync_buttons()
        self._delivery_panel._sync_delivery_buttons()

    def start_polling(self) -> None:
        Clock.schedule_once(lambda _dt: self._delivery_panel.refresh(), 0.4)
        tour_panel = (
            self._split.tour_panel
            if self._split
            else self._sm.get_screen("tour").panel  # type: ignore[union-attr]
        )
        if self._ctrl.mode == "api":

            def _poll_api(_dt: float) -> None:
                self._delivery_panel.refresh()
                tour_panel.poll_vehicle()

            def _poll_voice(_dt: float) -> None:
                self._poll_voice_ptt()

            self._poll = Clock.schedule_interval(_poll_api, 2.5)
            self._voice_poll = Clock.schedule_interval(_poll_voice, 0.35)
        else:
            self._poll = None
            self._voice_poll = None

    def stop_polling(self) -> None:
        if getattr(self, "_poll", None):
            self._poll.cancel()
            self._poll = None
        if getattr(self, "_voice_poll", None):
            self._voice_poll.cancel()
            self._voice_poll = None


class OnboardApp(App):
    cn_font: str = ""
    title = "NovaJoy · 车载集成端"

    def build(self) -> OnboardRoot:
        apply_window_theme()
        w, h = 1280, 720
        try:
            Window.size = (w, h)
            Window.softinput_mode = "below_target"
        except Exception:
            pass
        if not self.cn_font:
            self.cn_font = register_chinese_font()
        root = OnboardRoot(self)
        root.refresh_chrome()
        return root

    def on_start(self) -> None:
        root = self.root
        if isinstance(root, OnboardRoot):
            root.start_polling()

    def on_stop(self) -> None:
        root = self.root
        if isinstance(root, OnboardRoot):
            root.stop_polling()


def main() -> None:
    fn = register_chinese_font()
    if not fn:
        print(
            "警告: 未找到中文字体，部分中文可能显示为方框。"
            "请执行: python scripts/download_fonts.py"
        )
    app = OnboardApp()
    app.cn_font = fn
    app.run()


if __name__ == "__main__":
    main()
