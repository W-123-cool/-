"""导览 / 送货分页内容（触控布局）。"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, Optional

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from .onboard_state import get_controller
from .tour_state_machine import NaviState
from .fonts_kivy import apply_font
from novajoy_ui.theme import C, style_readonly_log

from .widgets import cn_label, field_input, safe_text, section_label, show_toast, style_button, touch_btn

if TYPE_CHECKING:
    from .main import OnboardRoot


def _run_bg(fn: Callable[[], Any], on_ok: Callable[[Any], None], on_err: Callable[[str], None]) -> None:
    def work() -> None:
        try:
            r = fn()
            Clock.schedule_once(lambda _dt, v=r: on_ok(v), 0)
        except Exception as e:
            Clock.schedule_once(lambda _dt, m=str(e): on_err(m), 0)

    threading.Thread(target=work, daemon=True).start()


class TourPanel(BoxLayout):
  def __init__(self, root: "OnboardRoot", font_name: str, **kwargs: Any) -> None:
    super().__init__(**kwargs)
    self.orientation = "vertical"
    self.spacing = dp(8)
    self.padding = (dp(8), dp(4), dp(8), dp(4))
    self._root = root
    self._fn = font_name
    self._ctrl = get_controller()
    self._selected_room = ""
    self._room_btns: dict[str, Any] = {}
    self._cancel_pending = False
    self._voice_recording = False
    self._ptt_partial = ""

    self._status = cn_label(
      "",
      font_name,
      size_hint_y=None,
      height=dp(52),
      font_size=dp(20),
      halign="left",
    )
    self.add_widget(self._status)

    self.lbl_mode = cn_label(
      "",
      font_name,
      size_hint_y=None,
      height=dp(36),
      font_size=dp(13),
      halign="left",
    )
    self.add_widget(self.lbl_mode)

    self.add_widget(section_label("选择目标房间（与真车地图一致）", font_name))
    room_scroll = ScrollView(size_hint_y=None, height=dp(120), do_scroll_x=False)
    self._room_grid = GridLayout(cols=4, spacing=dp(6), size_hint_y=None)
    self._room_grid.bind(minimum_height=self._room_grid.setter("height"))
    room_scroll.add_widget(self._room_grid)
    self.add_widget(room_scroll)

    row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40), spacing=dp(8))
    row.add_widget(cn_label("已选:", font_name, size_hint_x=0.12, font_size=dp(16)))
    self.lbl_selected = cn_label("—", font_name, size_hint_x=0.88, font_size=dp(18))
    row.add_widget(self.lbl_selected)
    self.add_widget(row)

    grid = GridLayout(cols=2, spacing=dp(8), size_hint_y=None)
    grid.bind(minimum_height=grid.setter("height"))
    self._btns: dict[str, Any] = {}
    defs = [
      ("go", "确认导览"),
      ("voice_wake", "语音输入（P3）"),
      ("voice_ptt", "按住录音（P3）"),
      ("cancel_user", "取消导航"),
      ("cancel_confirm", "确认取消导览"),
      ("simulate_arrived", "模拟到站"),
    ]
    for key, label in defs:
      b = touch_btn(label, font_name, lambda _w, k=key: self._act(k))
      self._btns[key] = b
      grid.add_widget(b)
    scroll_btns = ScrollView(size_hint_y=None, height=dp(168))
    grid.size_hint_y = None
    scroll_btns.add_widget(grid)
    self.add_widget(scroll_btns)

    self.add_widget(section_label("导览日志", font_name))
    self.log_out = TextInput(readonly=True, font_size=dp(14))
    apply_font(self.log_out, font_name)
    style_readonly_log(self.log_out)
    self.add_widget(self.log_out)

    self._ctrl.tour.on("on_state_change", self._on_tour_ui)
    self._ctrl.tour.on("on_log", self._append_tour_log)
    self._on_tour_ui(self._ctrl.tour.state, self._ctrl.tour.state_label)
    self._reload_room_buttons()

  def _reload_room_buttons(self) -> None:
    def work() -> None:
      if self._ctrl.mode == "api":
        self._ctrl.api_fetch_building_rooms()
      return self._ctrl.tour_selectable_room_ids()

    def on_ok(ids: Any) -> None:
      self._apply_room_ids(list(ids or []))

    def on_err(msg: str) -> None:
      self._apply_room_ids(self._ctrl.tour_selectable_room_ids())
      self._append_tour_log(f"房间表加载: {msg}")

    _run_bg(work, on_ok, on_err)

  def _apply_room_ids(self, ids: list[str]) -> None:
    self._room_grid.clear_widgets()
    self._room_btns.clear()
    for rid in ids:
      b = touch_btn(rid, self._fn, lambda _w, r=rid: self._pick_room(r))
      self._room_btns[rid] = b
      self._room_grid.add_widget(b)
    self._refresh_mode_label()
    self._sync_buttons()

  def _pick_room(self, room_id: str) -> None:
    if self._room_interaction_locked():
      show_toast("导览", "行进中不可选房间", self._fn)
      return
    self._selected_room = room_id.strip()
    self.lbl_selected.text = safe_text(self._selected_room or "—")
    for rid, btn in self._room_btns.items():
      style_button(btn, enabled=True, accent=(rid == self._selected_room))
    self._sync_buttons()

  def _room_interaction_locked(self) -> bool:
    st = self._ctrl.tour.state
    if st == NaviState.NAVIGATING and self._ctrl.tour_ui_locked():
      return True
    if st in (NaviState.RETURNING,):
      return True
    return False

  def _refresh_mode_label(self) -> None:
    msg = safe_text(self._ctrl.tour.message or "")
    deadline = self._ctrl.tour.deadline_remaining
    extra = ""
    if deadline is not None:
      extra = f" · 超时 {int(deadline)}s"
    if self._ctrl.mode == "local":
      base = "导览：本地演练（后端六态镜像）"
    elif self._ctrl.tour_uses_real_vehicle():
      base = "导览：真车 MQTT · 到站自动进原地待机"
    else:
      base = "导览：API 模式（无 MQTT 可点模拟到站）"
    self.lbl_mode.text = safe_text(base + (f" · {msg}" if msg else "") + extra)

  def poll_vehicle(self) -> None:
    self._ctrl.tour_poll_status()
    try:
      st = self._ctrl.voice_ptt_status()
      self._voice_recording = bool(st.get("recording"))
    except Exception:
      pass
    self._sync_buttons()

  def _append_tour_log(self, entry: str) -> None:
    Clock.schedule_once(lambda _dt: self._do_append_log(entry), 0)

  def _do_append_log(self, entry: str) -> None:
    cur = safe_text(self.log_out.text)
    self.log_out.text = (cur + "\n" + safe_text(entry)).strip()
    if hasattr(self._root, "append_log"):
      self._root.append_log(safe_text(entry))

  def _on_tour_ui(self, state: NaviState, label: Any) -> None:
    tgt = safe_text(self._ctrl.tour.target or "—")
    lock = " · 触控锁定" if self._ctrl.tour_ui_locked() else ""
    self._status.text = safe_text(f"导览 · {label} · 目标 {tgt}{lock}")
    if state not in (NaviState.HOLDING, NaviState.NAVIGATING):
      self._cancel_pending = False
    self._refresh_mode_label()
    self._sync_buttons()

  def _sync_buttons(self) -> None:
    st = self._ctrl.tour.state
    has_tgt = bool(self._selected_room.strip())
    ok_tour, tour_reason = self._ctrl.can_start_tour()
    ui_locked = self._ctrl.tour_ui_locked()
    room_locked = self._room_interaction_locked()
    can_cancel = st in (NaviState.NAVIGATING, NaviState.HOLDING)

    for rid, btn in self._room_btns.items():
      en = not room_locked
      btn.disabled = not en
      style_button(btn, enabled=en, accent=(rid == self._selected_room))

    if st == NaviState.RETURNING:
      rules = {k: False for k in self._btns}
    elif st == NaviState.NAVIGATING:
      rules = {k: False for k in self._btns}
      rules["cancel_user"] = not self._cancel_pending
      rules["cancel_confirm"] = self._cancel_pending
    elif ui_locked:
      rules = {k: False for k in self._btns}
    else:
      rules = {
        "go": st in {NaviState.IDLE, NaviState.WAITING_VOICE} and has_tgt and ok_tour,
        "voice_wake": st in {NaviState.IDLE, NaviState.HOLDING, NaviState.WAITING_VOICE},
        "voice_ptt": st in {NaviState.IDLE, NaviState.HOLDING, NaviState.WAITING_VOICE},
        "cancel_user": can_cancel and not self._cancel_pending,
        "cancel_confirm": can_cancel and self._cancel_pending,
        "simulate_arrived": self._ctrl.tour_simulate_arrive_allowed(),
      }
    for k, btn in self._btns.items():
      en = rules.get(k, False)
      btn.disabled = not en
      style_button(btn, enabled=en, accent=True)
    go_btn = self._btns.get("go")
    if go_btn is not None:
      if not ok_tour and tour_reason and st in {NaviState.IDLE, NaviState.WAITING_VOICE}:
        short = tour_reason if len(tour_reason) <= 16 else tour_reason[:16] + "…"
        go_btn.text = f"确认导览（{short}）"
      else:
        go_btn.text = "确认导览"
    cancel_btn = self._btns.get("cancel_user")
    if cancel_btn is not None:
      if st == NaviState.NAVIGATING:
        cancel_btn.text = "取消导览"
      else:
        cancel_btn.text = "取消导航"
    ptt = self._btns.get("voice_ptt")
    if ptt is not None:
      ptt.text = "刷新待机/待语音计时"
    voice_btn = self._btns.get("voice_wake")
    if voice_btn is not None:
      voice_btn.text = "结束录音" if self._voice_recording else "语音输入"

  def _act(self, key: str) -> None:
    if key == "go":
      ok, msg = self._ctrl.tour_begin_flow(self._selected_room)
    elif key == "cancel_user":
      self._cancel_pending = True
      self._append_tour_log("→ 请再次点击「确认取消导览」")
      self._sync_buttons()
      return
    elif key == "cancel_confirm":
      ok, msg = self._ctrl.tour_action("holding_cancel")
      if ok:
        self._cancel_pending = False
    elif key in ("voice_wake", "voice_ptt"):
      if key == "voice_ptt":
        ok, msg = self._ctrl.tour_action("voice_touch")
        if ok:
          show_toast("导览", "已刷新待机计时", self._fn)
        else:
          show_toast("导览", msg, self._fn)
        return
      self._root._on_voice_tap()
      return
    else:
      ok, msg = self._ctrl.tour_action(key)
    if not ok:
      show_toast("导览", msg, self._fn)
    else:
      self._root.refresh_chrome()
      if key != "go":
        self._append_tour_log(f"→ {msg}")

  def on_leave(self) -> None:
    self._cancel_pending = False


class DeliveryPanel(BoxLayout):
  def __init__(self, root: "OnboardRoot", font_name: str, **kwargs: Any) -> None:
    super().__init__(**kwargs)
    self.orientation = "vertical"
    self.spacing = dp(6)
    self.padding = (dp(8), dp(4), dp(8), dp(4))
    self._root = root
    self._fn = font_name
    self._ctrl = get_controller()
    self._refresh_lock = threading.Lock()

    scroll = ScrollView(do_scroll_x=False)
    inner = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
    inner.bind(minimum_height=inner.setter("height"))

    if self._ctrl.mode == "local":
      mode_txt = "仅导览演练（local）"
      hint = (
        "取货请求、到站取货请用取货端 user_client，并设 ONBOARD_MODE=api 启动本集成端联调。\n"
        "本模式不连接后端，下方送货操作不可用。"
      )
    else:
      mode_txt = f"联调 · {self._ctrl.api_base}"
      hint = (
        "取货：请另开窗口运行 python -m user_client.main（注册/登录后发起取货）。\n"
        "确认取货：在取货端输入任务 ID 与密码。本页仅送货员投件/送达/回位。"
      )
    inner.add_widget(section_label(f"送货 · {mode_txt}", font_name))
    self.lbl_hint = cn_label(
      hint,
      font_name,
      size_hint_y=None,
      height=dp(56),
      font_size=dp(14),
      halign="left",
      valign="top",
      color=C.text_secondary,
    )
    self.lbl_hint.bind(
      size=lambda w, *_: setattr(w, "text_size", (w.width - dp(8), None))
    )
    inner.add_widget(self.lbl_hint)

    inner.add_widget(section_label("待投件 / 送货中", font_name))
    self.out_queue = TextInput(readonly=True, size_hint_y=None, height=dp(160), font_size=dp(14))
    apply_font(self.out_queue, font_name)
    style_readonly_log(self.out_queue)
    inner.add_widget(self.out_queue)
    inner.add_widget(touch_btn("刷新队列", font_name, lambda *_: self.refresh()))

    inner.add_widget(section_label("确认投件", font_name))
    self.in_match = field_input(font_name)
    inner.add_widget(self.in_match)
    self.btn_confirm = touch_btn("确认投件", font_name, lambda *_: self._confirm())
    inner.add_widget(self.btn_confirm)

    inner.add_widget(section_label("标记已送达", font_name))
    self.in_task = field_input(font_name)
    inner.add_widget(self.in_task)
    self.btn_deliver = touch_btn("标记货物已送达", font_name, lambda *_: self._delivered())
    inner.add_widget(self.btn_deliver)

    inner.add_widget(section_label("维护", font_name))
    inner.add_widget(touch_btn("模拟回位", font_name, lambda *_: self._return_home()))
    inner.add_widget(touch_btn("清空全部任务", font_name, lambda *_: self._clear_all()))

    scroll.add_widget(inner)
    self.add_widget(scroll)

  def refresh(self, *_args: Any) -> None:
    if not self._refresh_lock.acquire(blocking=False):
      return
    if self._ctrl.mode == "local":
      Clock.schedule_once(lambda _dt: self._apply_local_only_hint(), 0)
      return

    def work() -> None:
      q, st, err = self._ctrl.api_fetch_queue_and_robot()
      Clock.schedule_once(lambda _dt, qq=q, ss=st, ee=err: self._apply_api(qq, ss, ee), 0)

    threading.Thread(target=work, daemon=True).start()

  def _release(self) -> None:
    try:
      self._refresh_lock.release()
    except RuntimeError:
      pass

  def _apply_local_only_hint(self) -> None:
    try:
      self.out_queue.text = "（local 模式未连后端；请用取货端 + ONBOARD_MODE=api 联调）"
    finally:
      self._release()
    self._sync_delivery_buttons()

  def _require_api(self) -> bool:
    if not self._ctrl.delivery_requires_api():
      return True
    show_toast(
      "送货联调",
      "当前为 local 模式，仅导览可用。请：\n"
      "1) 启动 backend\n"
      "2) $env:ONBOARD_MODE='api'\n"
      "3) 用 user_client 发起取货",
      self._fn,
    )
    return False

  def _apply_api(self, q: list, st: dict, err: Optional[str]) -> None:
    try:
      if err:
        self.out_queue.text = f"加载失败：{err}"
      else:
        from courier_client.status_labels import fmt_task_status

        lines = []
        tour = st.get("tour") if isinstance(st.get("tour"), dict) else {}
        if tour.get("tour_busy"):
          lines.append(f"[导览] {tour.get('phase_label_cn', '导览中')}")
        for t in q:
          lines.append(
            f"{t.get('id', '')[:8]}… | {t.get('door_plate')} | {t.get('match_key')} | "
            f"{fmt_task_status(str(t.get('status', '')))}"
          )
        self.out_queue.text = "\n".join(lines) if lines else "(队列为空)"
    finally:
      self._release()
    self._sync_delivery_buttons()
    self._root.refresh_chrome()

  def _sync_delivery_buttons(self) -> None:
    if self._ctrl.delivery_requires_api():
      ok, reason = False, "请使用 api 模式"
    else:
      ok, reason = self._ctrl.can_courier_dispatch()
    tour_idle = self._ctrl.tour_is_idle()
    for btn in (self.btn_confirm, self.btn_deliver):
      btn.disabled = not ok
      style_button(btn, enabled=ok, accent=True)
    if not tour_idle:
      label = self._ctrl.tour.state_label
      self.btn_confirm.text = f"确认投件（{label}）"
    elif not ok and reason:
      short = reason if len(reason) <= 14 else reason[:14] + "…"
      self.btn_confirm.text = f"确认投件（{short}）"
    else:
      self.btn_confirm.text = "确认投件"

  def _confirm(self) -> None:
    if not self._require_api():
      return

    def job() -> Any:
      return self._ctrl.api_courier_confirm(self.in_match.text)

    def ok(res: Any) -> None:
      success, msg, _ = res
      show_toast("投件", msg, self._fn)
      self.refresh()

    _run_bg(job, ok, lambda m: show_toast("投件失败", m, self._fn))

  def _delivered(self) -> None:
    tid = self.in_task.text.strip()
    if not self._require_api():
      return

    def job() -> Any:
      return self._ctrl.api_mark_delivered(tid)

    def ok(res: Any) -> None:
      ok2, msg = res
      show_toast("送达", msg, self._fn)
      self.refresh()

    _run_bg(job, ok, lambda m: show_toast("送达失败", m, self._fn))

  def _return_home(self) -> None:
    if not self._require_api():
      return

    def job() -> Any:
      return self._ctrl.api_return_home()

    def ok(res: Any) -> None:
      success, msg, _ = res
      show_toast("回位", msg, self._fn)
      self.refresh()

    _run_bg(job, ok, lambda m: show_toast("回位失败", m, self._fn))

  def _clear_all(self) -> None:
    if not self._require_api():
      return

    def job() -> Any:
      return self._ctrl.api_clear_all()

    def ok(msg: str) -> None:
      show_toast("清空", msg, self._fn)
      self.refresh()

    _run_bg(job, ok, lambda m: show_toast("清空失败", m, self._fn))