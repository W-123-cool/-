"""
取货用户端 — Kivy PC/平板版（双列控制台布局）。

手机请使用：`python -m user_client_mobile.main`

送货员投件/送达请使用 `python -m courier_client.main`。

- PC / RockPi：python3 -m user_client.main  （勿用 python，Ubuntu 上常为 2.7）
- 手机：python3 -m user_client_mobile.main
- Android APK：于 user_client 或 user_client_mobile 目录 buildozer android debug

环境变量 PICKUP_API_BASE 可覆盖默认 API 根地址。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout

from novajoy_ui import (
    ControlTopBar,
    MessageStream,
    NovaJoyCard,
    StatusStrip,
    apply_root_background,
    apply_window_theme,
    nvj_button,
    nvj_field_label,
    nvj_input,
    show_nvj_popup,
    C,
)

try:
    from . import api_client
    from .api_client import DEFAULT_BASE
    from .fonts_kivy import register_chinese_font
    from .status_labels import fmt_robot_state, fmt_task_status
except ImportError:
    import api_client  # type: ignore[no-redef]
    from api_client import DEFAULT_BASE  # type: ignore[no-redef]
    from fonts_kivy import register_chinese_font  # type: ignore[no-redef]
    from status_labels import fmt_robot_state, fmt_task_status  # type: ignore[no-redef]


def show_popup(title: str, message: str, font_name: str) -> None:
    show_nvj_popup(title, message, font_name)


def run_in_thread(
    fn: Callable[[], Any],
    on_ok: Callable[[Any], None],
    on_err: Callable[[str], None],
) -> None:
    def work() -> None:
        try:
            result = fn()
            Clock.schedule_once(lambda _dt, r=result: on_ok(r), 0)
        except Exception as e:
            err_msg = str(e)
            Clock.schedule_once(lambda _dt, m=err_msg: on_err(m), 0)

    threading.Thread(target=work, daemon=True).start()


class Root(BoxLayout):
    """控制台布局：顶栏 + 状态条 + 双列任务区 + 底部消息流。"""

    def __init__(self, app: "PickupApp", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._app = app
        self.orientation = "vertical"
        self.padding = (dp(8), dp(6), dp(8), dp(0))
        self.spacing = dp(8)
        apply_root_background(self)

        fn = app.cn_font

        self.top_bar = ControlTopBar(fn, page_name="取货终端", variant="logo")
        self.top_bar.add_chip("user", "未登录")
        self.top_bar.add_chip("link", "待连接")
        self.add_widget(self.top_bar)

        self.status = StatusStrip(fn, cols=4)
        self.status.add_metric("robot", "机器人", "—")
        self.status.add_metric("user", "用户", "未登录")
        self.status.add_metric("tasks", "任务", "0")
        self.status.add_metric("alert", "告警", "正常")
        self.add_widget(self.status)

        workspace = BoxLayout(
            orientation="horizontal",
            size_hint=(1, 1),
            spacing=dp(10),
            padding=(0, dp(4)),
        )

        left = BoxLayout(orientation="vertical", size_hint_x=0.46, spacing=dp(10))
        card_conn = NovaJoyCard(fn, title="连接")
        card_conn.content.add_widget(nvj_field_label("API 根地址（真机填局域网 IP:端口）", fn))
        self.in_base = nvj_input(fn, text=DEFAULT_BASE)
        card_conn.content.add_widget(self.in_base)
        left.add_widget(card_conn)

        card_auth = NovaJoyCard(fn, title="账户")
        card_auth.content.add_widget(nvj_field_label("用户名", fn))
        self.in_username = nvj_input(fn)
        card_auth.content.add_widget(self.in_username)
        card_auth.content.add_widget(nvj_field_label("登录密码", fn))
        self.in_pw = nvj_input(fn, password=True)
        card_auth.content.add_widget(self.in_pw)
        auth_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(52),
            spacing=dp(8),
        )
        auth_row.add_widget(
            nvj_button("注册并登录", fn, lambda *_: self._register(), primary=True)
        )
        auth_row.add_widget(nvj_button("登录", fn, lambda *_: self._login()))
        card_auth.content.add_widget(auth_row)
        left.add_widget(card_auth)
        workspace.add_widget(left)

        right = BoxLayout(orientation="vertical", size_hint_x=0.54, spacing=dp(10))
        card_req = NovaJoyCard(fn, title="发起取货")
        card_req.content.add_widget(nvj_field_label("门牌号", fn))
        self.in_door = nvj_input(fn)
        card_req.content.add_widget(self.in_door)
        card_req.content.add_widget(
            nvj_button("提交取货请求", fn, lambda *_: self._pickup_request(), primary=True)
        )
        right.add_widget(card_req)

        card_pick = NovaJoyCard(fn, title="到站取货")
        card_pick.content.add_widget(nvj_field_label("任务 ID", fn))
        self.in_task_pick = nvj_input(fn)
        card_pick.content.add_widget(self.in_task_pick)
        card_pick.content.add_widget(nvj_field_label("登录密码", fn))
        self.in_pick_pw = nvj_input(fn, password=True)
        card_pick.content.add_widget(self.in_pick_pw)
        card_pick.content.add_widget(
            nvj_button("确认取货", fn, lambda *_: self._verify_pickup(), primary=True)
        )
        card_pick.content.add_widget(
            nvj_button("刷新任务与通知", fn, lambda *_: self._refresh())
        )
        right.add_widget(card_pick)
        workspace.add_widget(right)

        self.add_widget(workspace)

        self.msg = MessageStream(fn, title="任务与通知", height=108)
        self.msg.append("系统就绪，请连接 API 并登录")
        self.add_widget(self.msg)

    def base(self) -> str:
        return self.in_base.text.strip().rstrip("/")

    def _set_user_ui(self, logged_in: bool, label: str = "") -> None:
        txt = label or ("已登录" if logged_in else "未登录")
        self.top_bar.set_chip("user", txt[:8])
        self.status.set_metric("user", txt, indicator=C.success if logged_in else C.text_secondary)

    def _register(self) -> None:
        def job() -> dict[str, Any]:
            return api_client.api_register(
                self.base(), self.in_username.text, self.in_pw.text
            )

        def ok(data: dict[str, Any]) -> None:
            self._app.auth_token = str(data.get("token", ""))
            self._set_user_ui(True, "已登录（注册）")
            self.msg.append("注册并登录成功")
            show_popup("成功", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh()

        def err(msg: str) -> None:
            self.msg.append(f"注册失败：{msg}")
            show_popup("注册失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)

    def _login(self) -> None:
        def job() -> dict[str, Any]:
            return api_client.api_login(
                self.base(), self.in_username.text, self.in_pw.text
            )

        def ok(data: dict[str, Any]) -> None:
            self._app.auth_token = str(data.get("token", ""))
            self._set_user_ui(True, "已登录")
            self.msg.append("登录成功")
            show_popup("成功", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh()

        def err(msg: str) -> None:
            self.msg.append(f"登录失败：{msg}")
            show_popup("登录失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)

    def _pickup_request(self) -> None:
        if not self._app.auth_token:
            show_popup("提示", "请先登录", self._app.cn_font)
            return

        def job() -> dict[str, Any]:
            return api_client.api_pickup_request(
                self.base(),
                self._app.auth_token,
                self.in_door.text,
            )

        def ok(data: dict[str, Any]) -> None:
            code = str(data.get("dropoff_code") or "")
            self.msg.append(f"取货请求已提交，投件码 {code}" if code else "取货请求已提交")
            show_popup("已提交", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh()

        def err(msg: str) -> None:
            self.msg.append(f"请求失败：{msg}")
            show_popup("请求失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)

    def _verify_pickup(self) -> None:
        if not self._app.auth_token:
            show_popup("提示", "请先登录", self._app.cn_font)
            return

        def job() -> dict[str, Any]:
            return api_client.api_pickup_verify(
                self.base(),
                self._app.auth_token,
                self.in_task_pick.text,
                self.in_pick_pw.text,
            )

        def ok(data: dict[str, Any]) -> None:
            self.msg.append("取货确认成功")
            show_popup("取货", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh()

        def err(msg: str) -> None:
            self.msg.append(f"取货失败：{msg}")
            show_popup("取货失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)

    def _refresh(self) -> None:
        base = self.base()
        self.top_bar.set_chip("link", "同步中…")

        def job() -> tuple[
            Optional[list[dict[str, Any]]],
            Optional[list[dict[str, Any]]],
            dict[str, Any],
        ]:
            robot = api_client.api_robot_state(base)
            tok = self._app.auth_token
            if not tok:
                return None, None, robot
            tasks = api_client.api_user_tasks(base, tok)
            notes = api_client.api_notifications(base, tok)
            return tasks, notes, robot

        def ok(payload: Any) -> None:
            tasks, notes, robot = payload
            rs = str(robot.get("robot_state", "") or "")
            rs_txt = fmt_robot_state(rs)
            self.status.set_metric("robot", rs_txt[:18], indicator=C.accent)
            self.top_bar.set_chip("link", "在线" if rs else "待机")

            stream_lines: list[str] = []
            if tasks is None or notes is None:
                self.status.set_metric("tasks", "—")
                self.status.set_metric("alert", "未登录", indicator=C.warning)
                stream_lines.append("机器人状态已刷新（未登录）")
                self.msg.text = "\n".join(stream_lines)
                return

            for t in tasks:
                st = fmt_task_status(str(t.get("status", "")))
                stream_lines.append(
                    f"任务 {t.get('id')} · {t.get('door_plate')} · 投件码 {t.get('match_key')} · {st}"
                )
            self.status.set_metric("tasks", str(len(tasks)))

            note_n = len(notes or [])
            self.status.set_metric("alert", f"{note_n} 条" if note_n else "正常")
            for n in (notes or [])[:8]:
                stream_lines.append(
                    f"[{n.get('created_at')}] {n.get('title')} — {n.get('body')}"
                )
            if not stream_lines:
                stream_lines.append("当前无进行中的任务")
            self.msg.text = "\n".join(stream_lines)

        def err(msg: str) -> None:
            self.top_bar.set_chip("link", "异常")
            self.status.set_metric("alert", "刷新失败", indicator=C.danger)
            self.msg.append(f"刷新失败：{msg}")

        run_in_thread(job, ok, err)


class PickupApp(App):
    """auth_token 避免与 Kivy 内部命名冲突；仅内存保存会话。"""

    cn_font: str = ""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.auth_token: Optional[str] = None

    def build(self) -> Root:
        apply_window_theme()
        try:
            Window.softinput_mode = "below_target"
        except Exception:
            pass
        self.cn_font = register_chinese_font()
        return Root(self)


def main() -> None:
    fn = register_chinese_font()
    if not fn:
        print(
            "警告: 未找到中文字体，中文可能显示为方框。"
            "请执行: python scripts/download_fonts.py"
        )
    app = PickupApp()
    app.cn_font = fn
    app.run()


if __name__ == "__main__":
    main()
