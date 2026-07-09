"""取货用户端 — 手机版（Bottom Nav + 分屏）。"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView

from novajoy_ui import (
    C,
    MessageStream,
    NovaJoyCard,
    NovaJoyStatusCard,
    apply_root_background,
    apply_window_theme,
    nvj_button,
    nvj_field_label,
    nvj_input,
    show_nvj_popup,
)
from novajoy_ui.mobile import BottomNavigation, MobileTopBar

from user_client.api_client import DEFAULT_BASE
from user_client.api_client import (
    api_login,
    api_notifications,
    api_pickup_request,
    api_pickup_verify,
    api_register,
    api_robot_state,
    api_user_tasks,
)
from user_client.fonts_kivy import apply_font_tree, register_chinese_font
from user_client.status_labels import (
    ROBOT_STATE_CN,
    fmt_robot_state,
    fmt_task_status,
)


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
            Clock.schedule_once(lambda _dt, m=str(e): on_err(m), 0)

    threading.Thread(target=work, daemon=True).start()


class MobileScreen(Screen):
    """带 App 引用与顶栏标题的基类。"""

    title = "NovaJoy"

    def bind_font(self, fn: str) -> None:
        """子类可扩展；默认递归应用整页字体。"""
        apply_font_tree(self, fn)

    def on_enter(self, *_args: Any) -> None:
        mgr = self.manager
        if mgr and mgr.parent and hasattr(mgr.parent, "mobile_top_bar"):
            mgr.parent.mobile_top_bar.set_title(self.title)
        app = App.get_running_app()
        if isinstance(app, PickupMobileApp):
            app.refresh_cache(lambda: self.on_data_updated())

    def on_data_updated(self) -> None:
        pass


class HomeScreen(MobileScreen):
    title = "首页"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="home", **kwargs)
        self._fn = ""
        outer = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(72), spacing=dp(12))
        self.card_robot = NovaJoyStatusCard("机器人", "—", "")
        self.card_task = NovaJoyStatusCard("进行中任务", "0", "")
        row.add_widget(self.card_robot)
        row.add_widget(self.card_task)
        outer.add_widget(row)

        self.hero = NovaJoyCard("", title="当前状态")
        self.lbl_hero = Label(
            text="登录后在「我的」完成账户配置，即可在此查看机器人与任务概况。",
            font_size=dp(14),
            halign="left",
            valign="top",
            size_hint_y=None,
            text_size=(Window.width - dp(64), None),
        )
        self.lbl_hero.bind(texture_size=lambda w, ts: setattr(w, "height", max(ts[1], dp(48))))
        self.hero.content.add_widget(self.lbl_hero)
        outer.add_widget(self.hero)

        self.btn_pickup = nvj_button("发起取货", "", self._go_tasks, primary=True, height=dp(56))
        self.btn_refresh = nvj_button("刷新状态", "", self._refresh, height=dp(52))
        outer.add_widget(self.btn_pickup)
        outer.add_widget(self.btn_refresh)

        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(outer)
        self.add_widget(scroll)

    def bind_font(self, fn: str) -> None:
        super().bind_font(fn)
        self._fn = fn

    def _go_tasks(self, *_args: Any) -> None:
        mgr = self.manager
        if mgr and mgr.parent and hasattr(mgr.parent, "bottom_nav"):
            mgr.parent.bottom_nav.select("tasks")
            mgr.current = "tasks"

    def _refresh(self, *_args: Any) -> None:
        app = App.get_running_app()
        if isinstance(app, PickupMobileApp):
            app.refresh_cache(lambda: self.on_data_updated())

    def on_data_updated(self) -> None:
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp):
            return
        rs_raw = str(app.cached_robot.get("robot_state", "") or "")
        rs_cn = ROBOT_STATE_CN.get(rs_raw, rs_raw or "—")
        self.card_robot.set_value(rs_cn, indicator=C.accent)
        n = len(app.cached_tasks)
        self.card_task.set_value(str(n), indicator=C.success if n else C.text_secondary)
        if not app.auth_token:
            self.lbl_hero.text = "未登录。请前往「我的」登录账户。"
            return
        if not app.cached_tasks:
            self.lbl_hero.text = "暂无进行中的任务。点击「发起取货」创建新任务。"
            return
        t = app.cached_tasks[0]
        tid = str(t.get("id", "") or "")
        st = fmt_task_status(str(t.get("status", "")))
        self.lbl_hero.text = (
            f"任务 ID\n{tid}\n"
            f"门牌 {t.get('door_plate')} · 投件码 {t.get('match_key')} · {st}\n"
            f"到站后请在「任务」页确认取货。"
        )


class TasksScreen(MobileScreen):
    title = "任务"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="tasks", **kwargs)
        self._fn = ""
        outer = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12), size_hint_y=None)
        outer.bind(minimum_height=outer.setter("height"))

        card_new = NovaJoyCard("", title="发起取货")
        self.in_door = nvj_input("")
        card_new.content.add_widget(nvj_field_label("门牌号", ""))
        card_new.content.add_widget(self.in_door)
        self.btn_submit = nvj_button("提交取货请求", "", self._submit, primary=True, height=dp(56))
        card_new.content.add_widget(self.btn_submit)
        outer.add_widget(card_new)

        card_verify = NovaJoyCard("", title="到站确认取货")
        self.in_task = nvj_input("")
        self.in_pick_pw = nvj_input("", password=True)
        card_verify.content.add_widget(nvj_field_label("任务 ID", ""))
        card_verify.content.add_widget(self.in_task)
        card_verify.content.add_widget(nvj_field_label("登录密码", ""))
        card_verify.content.add_widget(self.in_pick_pw)
        self.btn_verify = nvj_button("确认取货", "", self._verify, primary=True, height=dp(56))
        card_verify.content.add_widget(self.btn_verify)
        outer.add_widget(card_verify)

        self.card_list = NovaJoyCard("", title="我的任务")
        self.lbl_tasks = Label(
            text="登录后显示任务列表",
            font_size=dp(13),
            halign="left",
            valign="top",
            size_hint_y=None,
            color=C.text_secondary,
            markup=True,
        )
        self.lbl_tasks.bind(
            texture_size=lambda w, ts: setattr(w, "height", max(ts[1], dp(32))),
            size=lambda w, *_: setattr(w, "text_size", (w.width, None)),
        )
        self.card_list.content.add_widget(self.lbl_tasks)
        outer.add_widget(self.card_list)

        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(outer)
        self.add_widget(scroll)

    def bind_font(self, fn: str) -> None:
        super().bind_font(fn)
        self._fn = fn

    def on_data_updated(self) -> None:
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp):
            return
        if not app.auth_token:
            self.lbl_tasks.text = "未登录"
            return
        tasks = app.cached_tasks
        if not tasks:
            self.lbl_tasks.text = "暂无进行中的任务"
            self.in_task.text = ""
            return
        lines = []
        for i, t in enumerate(tasks):
            tid = str(t.get("id", "") or "")
            st = fmt_task_status(str(t.get("status", "")))
            door = t.get("door_plate", "—")
            lines.append(f"[b]{i + 1}.[/b] {tid}\n    门牌 {door} · 投件码 {t.get('match_key')} · {st}")
        self.lbl_tasks.text = "\n\n".join(lines)
        if not self.in_task.text.strip():
            self.in_task.text = str(tasks[0].get("id", "") or "")

    def _submit(self, *_args: Any) -> None:
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp) or not app.auth_token:
            show_popup("提示", "请先在「我的」登录", app.cn_font if isinstance(app, PickupMobileApp) else "")
            return

        def job() -> dict[str, Any]:
            return api_pickup_request(app.api_base, app.auth_token, self.in_door.text)

        def ok(data: dict[str, Any]) -> None:
            code = str(data.get("dropoff_code") or "")
            title = "已提交"
            body = json.dumps(data, ensure_ascii=False)
            if code:
                title = f"投件码 {code}"
            show_popup(title, body, app.cn_font)
            tid = str(data.get("task_id") or data.get("id") or "")
            if tid:
                self.in_task.text = tid
            app.refresh_cache(lambda: self.on_data_updated())

        def err(msg: str) -> None:
            show_popup("失败", msg, app.cn_font)

        run_in_thread(job, ok, err)

    def _verify(self, *_args: Any) -> None:
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp) or not app.auth_token:
            show_popup("提示", "请先在「我的」登录", app.cn_font)
            return

        def job() -> dict[str, Any]:
            return api_pickup_verify(
                app.api_base,
                app.auth_token,
                self.in_task.text,
                self.in_pick_pw.text,
            )

        def ok(data: dict[str, Any]) -> None:
            show_popup("取货成功", json.dumps(data, ensure_ascii=False), app.cn_font)
            app.refresh_cache()

        def err(msg: str) -> None:
            show_popup("失败", msg, app.cn_font)

        run_in_thread(job, ok, err)


class RobotScreen(MobileScreen):
    title = "机器人"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="robot", **kwargs)
        outer = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12), size_hint_y=None)
        outer.bind(minimum_height=outer.setter("height"))
        self.metrics: dict[str, NovaJoyStatusCard] = {}
        for key, title in (
            ("online", "在线状态"),
            ("state", "运行状态"),
            ("task", "任务状态"),
            ("net", "网络"),
        ):
            c = NovaJoyStatusCard(title, "—", "")
            self.metrics[key] = c
            outer.add_widget(c)
        self.btn_refresh = nvj_button("刷新", "", self._refresh, primary=True, height=dp(52))
        outer.add_widget(self.btn_refresh)
        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(outer)
        self.add_widget(scroll)

    def bind_font(self, fn: str) -> None:
        super().bind_font(fn)

    def _refresh(self, *_args: Any) -> None:
        app = App.get_running_app()
        if isinstance(app, PickupMobileApp):
            app.refresh_cache(lambda: self.on_data_updated())

    def on_data_updated(self) -> None:
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp):
            return
        rs_raw = str(app.cached_robot.get("robot_state", "") or "")
        self.metrics["online"].set_value("在线" if rs_raw else "待机", indicator=C.success if rs_raw else C.warning)
        self.metrics["state"].set_value(fmt_robot_state(rs_raw), indicator=C.accent)
        if app.cached_tasks:
            st = fmt_task_status(str(app.cached_tasks[0].get("status", "")))
            self.metrics["task"].set_value(st, indicator=C.accent_soft)
        else:
            self.metrics["task"].set_value("无任务", indicator=C.text_secondary)
        self.metrics["net"].set_value("已连接", indicator=C.success)


class MessagesScreen(MobileScreen):
    title = "消息"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="messages", **kwargs)
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        self.stream = MessageStream("", title="", height=400)
        self.stream.size_hint_y = 1
        self.btn_refresh = nvj_button("刷新通知", "", self._refresh, height=dp(52))
        box.add_widget(self.stream)
        box.add_widget(self.btn_refresh)
        self.add_widget(box)

    def bind_font(self, fn: str) -> None:
        super().bind_font(fn)

    def _refresh(self, *_args: Any) -> None:
        app = App.get_running_app()
        if isinstance(app, PickupMobileApp):
            app.refresh_cache(lambda: self.on_data_updated())

    def on_data_updated(self) -> None:
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp):
            return
        if not app.auth_token:
            self.stream.text = "未登录，暂无通知"
            return
        lines = []
        for n in app.cached_notes[:30]:
            lines.append(f"[{n.get('created_at')}] {n.get('title')} — {n.get('body')}")
        self.stream.text = "\n".join(lines) if lines else "暂无通知"


class ProfileScreen(MobileScreen):
    title = "我的"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="profile", **kwargs)
        outer = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12), size_hint_y=None)
        outer.bind(minimum_height=outer.setter("height"))

        card_srv = NovaJoyCard("", title="服务器")
        self.in_base = nvj_input("", text=DEFAULT_BASE)
        card_srv.content.add_widget(nvj_field_label("API 根地址", ""))
        card_srv.content.add_widget(self.in_base)
        outer.add_widget(card_srv)

        card_acc = NovaJoyCard("", title="账户")
        self.in_username = nvj_input("")
        self.in_pw = nvj_input("", password=True)
        self.lbl_sess = Label(
            text="未登录",
            font_size=dp(14),
            size_hint_y=None,
            height=dp(24),
            halign="left",
            color=C.text_secondary,
        )
        card_acc.content.add_widget(nvj_field_label("用户名", ""))
        card_acc.content.add_widget(self.in_username)
        card_acc.content.add_widget(nvj_field_label("登录密码", ""))
        card_acc.content.add_widget(self.in_pw)
        card_acc.content.add_widget(self.lbl_sess)
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.btn_reg = nvj_button("注册并登录", "", self._register, primary=True)
        self.btn_login = nvj_button("登录", "", self._login)
        row.add_widget(self.btn_reg)
        row.add_widget(self.btn_login)
        card_acc.content.add_widget(row)
        outer.add_widget(card_acc)

        card_about = NovaJoyCard("", title="关于 NovaJoy")
        about = Label(
            text="NovaJoy · Smart Building Robotics\n楼宇具身智能服务机器人平台",
            font_size=dp(13),
            halign="left",
            valign="top",
            size_hint_y=None,
            color=C.text_secondary,
        )
        about.bind(texture_size=lambda w, ts: setattr(w, "height", max(ts[1], dp(40))))
        card_about.content.add_widget(about)
        outer.add_widget(card_about)

        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(outer)
        self.add_widget(scroll)

    def bind_font(self, fn: str) -> None:
        super().bind_font(fn)

    def on_enter(self, *_args: Any) -> None:
        super().on_enter()
        app = App.get_running_app()
        if isinstance(app, PickupMobileApp):
            self.in_base.text = app.api_base
            self.lbl_sess.text = "已登录" if app.auth_token else "未登录"
            self.lbl_sess.color = C.success if app.auth_token else C.text_secondary

    def _save_base(self) -> None:
        app = App.get_running_app()
        if isinstance(app, PickupMobileApp):
            app.api_base = self.in_base.text.strip().rstrip("/")

    def _register(self, *_args: Any) -> None:
        self._save_base()
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp):
            return

        def job() -> dict[str, Any]:
            return api_register(app.api_base, self.in_username.text, self.in_pw.text)

        def ok(data: dict[str, Any]) -> None:
            app.auth_token = str(data.get("token", ""))
            self.lbl_sess.text = "已登录（注册）"
            self.lbl_sess.color = C.success
            show_popup("成功", json.dumps(data, ensure_ascii=False), app.cn_font)
            app.refresh_cache()

        def err(msg: str) -> None:
            show_popup("注册失败", msg, app.cn_font)

        run_in_thread(job, ok, err)

    def _login(self, *_args: Any) -> None:
        self._save_base()
        app = App.get_running_app()
        if not isinstance(app, PickupMobileApp):
            return

        def job() -> dict[str, Any]:
            return api_login(app.api_base, self.in_username.text, self.in_pw.text)

        def ok(data: dict[str, Any]) -> None:
            app.auth_token = str(data.get("token", ""))
            self.lbl_sess.text = "已登录"
            self.lbl_sess.color = C.success
            show_popup("成功", json.dumps(data, ensure_ascii=False), app.cn_font)
            app.refresh_cache()

        def err(msg: str) -> None:
            show_popup("登录失败", msg, app.cn_font)

        run_in_thread(job, ok, err)


class MobileRoot(BoxLayout):
    def __init__(self, app: "PickupMobileApp", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.orientation = "vertical"
        apply_root_background(self)
        fn = app.cn_font

        self.mobile_top_bar = MobileTopBar(fn, title="首页")
        self.add_widget(self.mobile_top_bar)

        self.sm = ScreenManager(transition=NoTransition(), size_hint=(1, 1))
        self._screens = {
            "home": HomeScreen(),
            "tasks": TasksScreen(),
            "robot": RobotScreen(),
            "messages": MessagesScreen(),
            "profile": ProfileScreen(),
        }
        for sc in self._screens.values():
            sc.bind_font(fn)
            self.sm.add_widget(sc)
        self.sm.current = "home"
        self.add_widget(self.sm)

        def _nav(key: str) -> None:
            self.sm.current = key
            sc = self._screens.get(key)
            if sc:
                self.mobile_top_bar.set_title(sc.title)

        self.bottom_nav = BottomNavigation(fn, on_select=_nav)
        self.add_widget(self.bottom_nav)

        apply_font_tree(self, fn)


class PickupMobileApp(App):
    title = "NovaJoy · 取货"
    cn_font: str = ""
    auth_token: Optional[str] = None
    api_base: str = DEFAULT_BASE
    cached_tasks: list[dict[str, Any]]
    cached_notes: list[dict[str, Any]]
    cached_robot: dict[str, Any]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cached_tasks = []
        self.cached_notes = []
        self.cached_robot = {}
        self._refresh_callbacks: list[Callable[[], None]] = []

    def build(self) -> MobileRoot:
        apply_window_theme()
        try:
            Window.softinput_mode = "below_target"
            from kivy.utils import platform as kivy_platform

            if kivy_platform not in ("android", "ios"):
                Window.size = (390, 844)
        except Exception:
            pass
        if not self.cn_font:
            self.cn_font = register_chinese_font()
        return MobileRoot(self)

    def refresh_cache(self, on_done: Optional[Callable[[], None]] = None) -> None:
        base = self.api_base.strip().rstrip("/")

        def job() -> tuple[Any, Any, dict[str, Any]]:
            robot = api_robot_state(base)
            if not self.auth_token:
                return None, None, robot
            tasks = api_user_tasks(base, self.auth_token)
            notes = api_notifications(base, self.auth_token)
            return tasks, notes, robot

        def ok(payload: Any) -> None:
            tasks, notes, robot = payload
            self.cached_robot = robot or {}
            self.cached_tasks = list(tasks or []) if tasks is not None else []
            self.cached_notes = list(notes or []) if notes is not None else []
            if on_done:
                on_done()
            cur = self.root.sm.get_screen(self.root.sm.current) if self.root else None
            if cur and hasattr(cur, "on_data_updated"):
                cur.on_data_updated()

        def err(_msg: str) -> None:
            if on_done:
                on_done()

        run_in_thread(job, ok, err)


def main() -> None:
    fn = register_chinese_font()
    if not fn:
        print(
            "警告: 未找到中文字体，中文可能显示为方框。"
            "请执行: python scripts/download_fonts.py"
        )
    app = PickupMobileApp()
    app.cn_font = fn
    app.run()


if __name__ == "__main__":
    main()
