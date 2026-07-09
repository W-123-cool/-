"""
送货员 / 车载操作端 — Kivy（与取货端相同依赖，便于 PC 与 arm64 Ubuntu 20.04 直接跑）。

运行（项目根目录）:
    python3 -m courier_client.main   # 勿用 python，Ubuntu 上常为 2.7
    # 或: bash run_courier_client.sh

环境变量 COURIER_API_BASE 或 PICKUP_API_BASE 可设默认 API 根地址。
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable

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
    from . import api
    from .api import DEFAULT_BASE
    from .fonts_kivy import register_chinese_font
    from .status_labels import fmt_robot_state, fmt_task_status
except ImportError:
    import api  # type: ignore[no-redef]
    from api import DEFAULT_BASE  # type: ignore[no-redef]
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
    def __init__(self, app: "CourierApp", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._app = app
        self.orientation = "vertical"
        self.padding = (dp(8), dp(6), dp(8), dp(0))
        self.spacing = dp(8)
        apply_root_background(self)

        fn = app.cn_font

        self.top_bar = ControlTopBar(fn, page_name="送货调度", variant="logo")
        self.top_bar.add_chip("robot", "—")
        self.top_bar.add_chip("sync", "2.5s")
        self.add_widget(self.top_bar)

        self.status = StatusStrip(fn, cols=4)
        self.status.add_metric("robot", "机器人", "—")
        self.status.add_metric("queue", "队列", "0")
        self.status.add_metric("net", "网络", "—")
        self.status.add_metric("mode", "模式", "调度")
        self.add_widget(self.status)

        body = BoxLayout(orientation="horizontal", size_hint=(1, 1), spacing=dp(10))

        nav = BoxLayout(orientation="vertical", size_hint_x=0.34, spacing=dp(10))
        card_conn = NovaJoyCard(fn, title="连接")
        card_conn.content.add_widget(nvj_field_label("API 根地址", fn))
        self.in_base = nvj_input(fn, text=DEFAULT_BASE)
        card_conn.content.add_widget(self.in_base)
        card_conn.content.add_widget(
            nvj_button("刷新队列与状态", fn, lambda *_: self._refresh_all(), primary=True)
        )
        nav.add_widget(card_conn)

        card_maint = NovaJoyCard(fn, title="维护")
        card_maint.content.add_widget(
            nvj_button("模拟回位", fn, lambda *_: self._return_home())
        )
        card_maint.content.add_widget(
            nvj_button("清空全部任务", fn, lambda *_: self._clear_all(), danger=True)
        )
        nav.add_widget(card_maint)
        body.add_widget(nav)

        work = BoxLayout(orientation="vertical", size_hint_x=0.66, spacing=dp(10))
        card_confirm = NovaJoyCard(fn, title="确认投件")
        card_confirm.content.add_widget(nvj_field_label("投件码（6 位数字）", fn))
        self.in_match = nvj_input(fn)
        card_confirm.content.add_widget(self.in_match)
        self.btn_confirm = nvj_button("确认投件", fn, lambda *_: self._confirm(), primary=True)
        card_confirm.content.add_widget(self.btn_confirm)
        work.add_widget(card_confirm)

        card_deliver = NovaJoyCard(fn, title="标记已送达")
        card_deliver.content.add_widget(nvj_field_label("任务 ID", fn))
        self.in_task = nvj_input(fn)
        card_deliver.content.add_widget(self.in_task)
        card_deliver.content.add_widget(
            nvj_button("标记货物已送达", fn, lambda *_: self._delivered(), primary=True)
        )
        work.add_widget(card_deliver)

        card_queue = NovaJoyCard(fn, title="待投件 / 送货中")
        self.queue_stream = MessageStream(fn, title="", height=140, max_lines=8)
        self.queue_stream.height = dp(140)
        card_queue.content.add_widget(self.queue_stream)
        work.add_widget(card_queue)
        body.add_widget(work)

        self.add_widget(body)

        self.msg = MessageStream(fn, title="调度消息", height=96)
        self.msg.append("送货调度台就绪，每 2.5 秒自动同步")
        self.add_widget(self.msg)

        self._refresh_lock = threading.Lock()
        self._courier_dispatch_ok = True
        self._courier_dispatch_reason = ""

    def _sync_confirm_button(self) -> None:
        ok = getattr(self, "_courier_dispatch_ok", True)
        reason = getattr(self, "_courier_dispatch_reason", "")
        btn = getattr(self, "btn_confirm", None)
        if btn is None:
            return
        btn.disabled = not ok
        if not ok and reason:
            short = reason if len(reason) <= 18 else reason[:18] + "…"
            btn.text = f"确认投件（{short}）"
        else:
            btn.text = "确认投件"

    def base(self) -> str:
        return self.in_base.text.strip().rstrip("/")

    def _refresh_all(self, *_args: Any) -> None:
        if not self._refresh_lock.acquire(blocking=False):
            return
        b = self.base()
        self.top_bar.set_chip("sync", "同步…")

        def work() -> None:
            try:
                q = api.api_queue(b)
                st = api.api_robot_state(b)
                Clock.schedule_once(lambda _dt, qq=q, ss=st: self._apply_refresh_ok(qq, ss), 0)
            except Exception as e:
                Clock.schedule_once(lambda _dt, m=str(e): self._apply_refresh_err(m), 0)

        threading.Thread(target=work, daemon=True).start()

    def _apply_refresh_ok(
        self, q: list[dict[str, Any]], st: dict[str, Any]
    ) -> None:
        try:
            rs = str(st.get("robot_state", "") or "")
            rs_txt = fmt_robot_state(rs)
            tour_busy = bool(st.get("tour_busy"))
            tour_label = str(st.get("tour_phase_label_cn") or "")
            caps = st.get("capabilities") if isinstance(st.get("capabilities"), dict) else {}
            can_courier = caps.get("can_courier_dispatch")
            if can_courier is None:
                can_courier = not tour_busy and rs == "pending_delivery"
            courier_block = str(caps.get("can_courier_dispatch_reason") or "")
            if tour_busy and not courier_block:
                courier_block = f"导览中（{tour_label}）"

            self.status.set_metric("robot", rs_txt[:16], indicator=C.accent)
            self.top_bar.set_chip("robot", rs[:10] or "idle")
            self.status.set_metric("queue", str(len(q)))
            self.status.set_metric("net", "已连接", indicator=C.success)
            if tour_busy:
                self.status.set_metric("mode", tour_label[:8] or "导览", indicator=C.warning)
                self.top_bar.set_chip("sync", "导览")
            else:
                self.status.set_metric("mode", "调度")
                self.top_bar.set_chip("sync", "OK")

            lines = []
            if tour_busy:
                lines.append(f"[导览占用] {tour_label}")
            for t in q:
                status_line = fmt_task_status(str(t.get("status", "")))
                lines.append(
                    f"{t.get('id')} · {t.get('door_plate')} · {t.get('match_key')} · {status_line}"
                )
            self.queue_stream.text = "\n".join(lines) if lines else "(队列为空)"

            self._courier_dispatch_ok = bool(can_courier)
            self._courier_dispatch_reason = courier_block
        finally:
            self._refresh_lock.release()
        self._sync_confirm_button()

    def _apply_refresh_err(self, msg: str) -> None:
        try:
            self.status.set_metric("net", "失败", indicator=C.danger)
            self.top_bar.set_chip("sync", "ERR")
            self.queue_stream.text = f"加载失败：{msg}"
            self.msg.append(f"同步失败：{msg}")
        finally:
            self._refresh_lock.release()

    def _confirm(self) -> None:
        if not getattr(self, "_courier_dispatch_ok", True):
            reason = getattr(self, "_courier_dispatch_reason", "当前不可投件")
            show_popup("投件", reason, self._app.cn_font)
            return

        def job() -> dict[str, Any]:
            return api.api_confirm(self.base(), self.in_match.text)

        def ok(data: dict[str, Any]) -> None:
            self.msg.append("投件操作完成")
            show_popup("投件", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh_all()

        def err(msg: str) -> None:
            self.msg.append(f"投件失败：{msg}")
            show_popup("投件失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)

    def _delivered(self) -> None:
        tid = self.in_task.text.strip()

        def job() -> dict[str, Any]:
            return api.api_mark_delivered(self.base(), tid)

        def ok(data: dict[str, Any]) -> None:
            self.msg.append(f"任务 {tid[:8]}… 已标记送达")
            show_popup("送达", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh_all()

        def err(msg: str) -> None:
            self.msg.append(f"送达失败：{msg}")
            show_popup("送达失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)

    def _return_home(self) -> None:
        def job() -> dict[str, Any]:
            return api.api_robot_return_home(self.base())

        def ok(data: dict[str, Any]) -> None:
            self.msg.append("模拟回位完成")
            show_popup("模拟回位", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh_all()

        def err(msg: str) -> None:
            self.msg.append(f"回位失败：{msg}")
            show_popup("模拟回位失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)

    def _clear_all(self) -> None:
        def job() -> dict[str, Any]:
            return api.api_debug_clear_all_tasks(self.base())

        def ok(data: dict[str, Any]) -> None:
            self.msg.append("已清空全部任务")
            show_popup("已清空", json.dumps(data, ensure_ascii=False), self._app.cn_font)
            self._refresh_all()

        def err(msg: str) -> None:
            self.msg.append(f"清空失败：{msg}")
            show_popup("清空失败", msg, self._app.cn_font)

        run_in_thread(job, ok, err)


class CourierApp(App):
    cn_font: str = ""
    _poll_ev = None

    def build(self) -> Root:
        apply_window_theme()
        try:
            Window.softinput_mode = "below_target"
        except Exception:
            pass
        self.cn_font = register_chinese_font()
        root = Root(self)
        Clock.schedule_once(lambda _dt: root._refresh_all(), 0.3)
        return root

    def on_start(self) -> None:
        def _tick(dt: Any) -> None:
            root = self.root
            if root is not None:
                root._refresh_all(dt)

        self._poll_ev = Clock.schedule_interval(_tick, 2.5)

    def on_stop(self) -> None:
        if self._poll_ev is not None:
            self._poll_ev.cancel()
            self._poll_ev = None


def main() -> None:
    CourierApp().run()


if __name__ == "__main__":
    main()
