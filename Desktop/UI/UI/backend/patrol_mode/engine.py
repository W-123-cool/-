"""巡逻模式核心状态机。"""
from __future__ import annotations

import time
from typing import Any, Optional

from patrol_mode.config import (
    ENTRY_ROOM_ID,
    MOCK_NAV_SEC,
    NEAREST_WP_INTERVAL_SEC,
    WAIT_TASK_TIMEOUT_SEC,
    mock_vehicle_enabled,
)
from patrol_mode.models import (
    FirstMode,
    GuardPhase,
    PatrolModeSwitch,
    PatrolRuntimeState,
    PatrolSubState,
    PatrolTaskConfig,
)
from patrol_mode.nearest_wp import nearest_waypoint
from patrol_mode.track_handler import get_track_coordinator
from patrol_mode.vision_settings import load_vision_settings, save_vision_settings
from patrol_mode.nav_helpers import (
    capture_preempt_snapshot,
    nav_to_entry,
    preempt_tour_and_delivery,
    resume_after_patrol_exit,
    robot_at_home,
    save_task_config,
)
from patrol_mode.scheduler import active_schedule
from patrol_mode.patrol_executor import get_patrol_executor


class PatrolEngine:
    def __init__(self) -> None:
        from patrol_mode.nav_helpers import load_task_config

        self._state = PatrolRuntimeState(task=load_task_config())
        self._vision = load_vision_settings()

    @property
    def state(self) -> PatrolRuntimeState:
        return self._state

    def security_active(self) -> bool:
        return self._state.mode_switch == PatrolModeSwitch.ON

    def master_mode_label(self) -> str:
        if not self.security_active():
            return "idle"
        if self._state.sub_state in (PatrolSubState.GUARD, PatrolSubState.GUARD_VIEW_TRACK, PatrolSubState.GUARD_TIMER):
            return "guard"
        if self._state.sub_state == PatrolSubState.TRACK:
            return "track"
        return "patrol"

    def status_dict(self) -> dict[str, Any]:
        st = self._state.to_dict()
        st["security_active"] = self.security_active()
        st["master_mode"] = self.master_mode_label()
        st["mock_vehicle"] = mock_vehicle_enabled()
        st["entry_room"] = ENTRY_ROOM_ID
        sched = active_schedule()
        st["schedule_window_active"] = sched is not None
        st["schedule_active"] = sched.to_dict() if sched else None
        st["patrol_route"] = get_patrol_executor().progress_dict()
        from patrol_mode.alerts import get_alert_store

        st["alerts_unread"] = get_alert_store().unread_count()
        st["alerts_total"] = get_alert_store().total_count()
        st["vision_settings"] = self._vision.to_dict()
        return st

    def vision_settings_dict(self) -> dict[str, Any]:
        return self._vision.to_dict()

    def update_vision_toggles(
        self,
        *,
        patrol_track_enabled: Optional[bool] = None,
        guard_view_track_enabled: Optional[bool] = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        changed = False
        if patrol_track_enabled is not None:
            prev = self._vision.patrol_track_enabled
            self._vision.patrol_track_enabled = bool(patrol_track_enabled)
            changed = changed or prev != self._vision.patrol_track_enabled
            if prev and not self._vision.patrol_track_enabled:
                if self._state.sub_state == PatrolSubState.TRACK:
                    get_track_coordinator().stop_track("track_disabled")
                    self._state.sub_state = PatrolSubState.PATROL
                    self._state.track_phase = "idle"
                    self._state.track_resume_index = None
                    self._state.track_resume_label = ""
                    self._state.message = "巡逻追人已关闭，已停止 TRACK"
        if guard_view_track_enabled is not None:
            prev = self._vision.guard_view_track_enabled
            self._vision.guard_view_track_enabled = bool(guard_view_track_enabled)
            changed = changed or prev != self._vision.guard_view_track_enabled
        if not changed:
            ok, msg = self.sync_vision_to_vehicle()
            if ok:
                return True, msg or "设置未变化（已重同步车端）", self.status_dict()
            return True, f"设置未变化；车端同步失败: {msg}", self.status_dict()
        save_vision_settings(self._vision)
        ok, msg = self.sync_vision_to_vehicle()
        if not ok:
            return True, f"设置已保存；车端同步失败: {msg}", self.status_dict()
        return True, msg or "追人设置已保存并同步车端", self.status_dict()

    def apply_detection_conf(self, conf: float) -> tuple[bool, str, dict[str, Any]]:
        conf = max(0.05, min(0.95, float(conf)))
        self._vision.detection_conf = conf
        save_vision_settings(self._vision)
        ok, msg = self._publish_vision_config_to_vehicle(conf=conf)
        if not ok:
            return False, msg, self.status_dict()
        return True, f"识别精度已设为 {conf:.2f}（已下发车端一次）", self.status_dict()

    def _publish_vision_config_to_vehicle(
        self,
        *,
        conf: Optional[float] = None,
        guard_view_track_enabled: Optional[bool] = None,
        patrol_track_enabled: Optional[bool] = None,
    ) -> tuple[bool, str]:
        if mock_vehicle_enabled():
            return True, "mock 车端已接受视觉配置"
        payload: dict[str, Any] = {}
        if conf is not None:
            payload["conf"] = float(conf)
        if guard_view_track_enabled is not None:
            payload["guard_view_track_enabled"] = bool(guard_view_track_enabled)
        if patrol_track_enabled is not None:
            payload["patrol_track_enabled"] = bool(patrol_track_enabled)
        if not payload:
            return True, "无可下发项"
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if not bridge_enabled():
                return False, "MQTT 未连接，无法下发视觉配置"
            get_bridge().publish_patrol_vision_config(**payload)
        except Exception as e:
            return False, f"下发视觉配置失败: {e}"
        return True, "视觉配置已下发车端"

    def sync_vision_to_vehicle(self) -> tuple[bool, str]:
        """将 PC 当前视觉设置推送到车端（进入巡逻 / 后端启动时调用）。"""
        return self._publish_vision_config_to_vehicle(
            conf=self._vision.detection_conf,
            guard_view_track_enabled=self._vision.guard_view_track_enabled,
            patrol_track_enabled=self._vision.patrol_track_enabled,
        )

    def enter(
        self,
        *,
        first_mode: Optional[str] = None,
        patrol_rounds: Optional[int] = None,
        guard_between_min: Optional[int] = None,
        guard_yaw: Optional[float] = None,
        via: str = "manual",
    ) -> tuple[bool, str, dict[str, Any]]:
        if self.security_active():
            return False, "已在巡逻模式中", self.status_dict()

        task = self._state.task
        if first_mode is not None:
            task.first_mode = first_mode
        if patrol_rounds is not None:
            task.patrol_rounds = max(0, int(patrol_rounds))
        if guard_between_min is not None:
            task.guard_between_min = max(0, int(guard_between_min))
        if guard_yaw is not None:
            task.guard_yaw = float(guard_yaw)

        logs = preempt_tour_and_delivery()
        already_home = False
        if not mock_vehicle_enabled():
            try:
                already_home = robot_at_home()
            except Exception:
                already_home = False
        if already_home:
            logs.append("已在起点附近，跳过回 100")
        else:
            nav_to_entry("patrol_enter")

        sched = active_schedule()
        self._state = PatrolRuntimeState(
            mode_switch=PatrolModeSwitch.ON,
            sub_state=PatrolSubState.RETURN_PREP,
            message="返回起点、准备巡逻/驻守" if not already_home else "已在起点，准备巡逻/驻守",
            patrol_epoch=self._state.patrol_epoch + 1,
            preempt=capture_preempt_snapshot(),
            task=task,
            entered_via=via,
            manual_enter_until_end=via == "manual" and sched is not None,
            schedule_active_name=sched.name if sched and via == "schedule" else "",
        )
        save_task_config(task)
        if already_home:
            self._enter_guard_or_patrol_after_home()
        elif mock_vehicle_enabled():
            self._state.mock_nav_deadline_mono = time.monotonic() + MOCK_NAV_SEC

        detail = "已进入巡逻模式；" + "；".join(logs) if logs else "已进入巡逻模式"
        try:
            from patrol_mode.service import publish_patrol_master_status_now

            self.sync_vision_to_vehicle()
            publish_patrol_master_status_now()
        except Exception:
            pass
        return True, detail, self.status_dict()

    def exit(self, *, via: str = "manual") -> tuple[bool, str, dict[str, Any]]:
        if not self.security_active():
            return False, "当前未在巡逻模式", self.status_dict()

        # 先停 TRACK / 释放 GUARD 对 /cmd_vel 的占用，再恢复送货或回 100（顺序反了会 cancel 刚下发的导航）
        get_track_coordinator().stop_track("exit_security")
        get_patrol_executor().stop()
        msg, logs = resume_after_patrol_exit(self._state.preempt)
        sched = active_schedule()
        manual_block = via == "manual" and sched is not None

        self._state = PatrolRuntimeState(
            patrol_epoch=self._state.patrol_epoch,
            task=self._state.task,
            manual_block_auto_enter=manual_block,
        )
        try:
            from patrol_mode.service import publish_patrol_master_status_now

            publish_patrol_master_status_now()
        except Exception:
            pass
        detail = msg + ("；" + "；".join(logs) if logs else "")
        if manual_block:
            detail += "；本时段内不再自动进入"
        return True, detail, self.status_dict()

    def _enter_guard_or_patrol_after_home(self) -> None:
        task = self._state.task
        if task.first_mode == FirstMode.GUARD.value or task.patrol_rounds <= 0:
            self._state.sub_state = PatrolSubState.GUARD
            self._state.guard_phase = GuardPhase.IDLE.value
            self._state.message = "驻守待命（可手动控向；识人则视角跟踪）"
            get_track_coordinator().publish_motion_for_substate(PatrolSubState.GUARD)
            return
        self._state.sub_state = PatrolSubState.PATROL
        get_track_coordinator().publish_motion_for_substate(PatrolSubState.PATROL)
        ok, msg = get_patrol_executor().start_round(self._state.patrol_epoch)
        self._state.message = msg if ok else f"巡逻启动失败: {msg}"

    def on_patrol_round_complete(self) -> None:
        if not self.security_active():
            return
        get_patrol_executor().stop()
        self._state.sub_state = PatrolSubState.END_RETURN
        self._state.message = "本轮巡逻完成，返回起点"
        self._state.rounds_done += 1
        nav_to_entry("patrol_round_end")
        if mock_vehicle_enabled():
            self._state.mock_nav_deadline_mono = time.monotonic() + MOCK_NAV_SEC

    def tick(self) -> None:
        if not self.security_active():
            self._tick_scheduler_off()
            return
        self._tick_nearest_waypoint()
        self._tick_sub_state()
        self._tick_scheduler_on()

    def _tick_nearest_waypoint(self) -> None:
        if mock_vehicle_enabled():
            return
        now = time.monotonic()
        if (
            self._state.last_nearest_wp_mono is not None
            and now - self._state.last_nearest_wp_mono < NEAREST_WP_INTERVAL_SEC
        ):
            return
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if not bridge_enabled():
                return
            hb = get_bridge().snapshot()
            px, py = hb.get("pose_x"), hb.get("pose_y")
            if px is None or py is None:
                return
            near = nearest_waypoint(float(px), float(py), floor=str(hb.get("current_floor", "")))
            if near:
                self._state.nearest_wp_index = near.get("index")
                self._state.nearest_wp_label = str(near.get("label", ""))
                self._state.nearest_wp_distance_m = near.get("distance_m")
            self._state.last_nearest_wp_mono = now
        except Exception:
            pass

    def _person_alert_allowed(self, data: dict) -> bool:
        vs = self._vision
        hint = str(data.get("sub_state_hint", "") or "").strip().lower()
        sub = self._state.sub_state
        if hint == "guard" or sub in (
            PatrolSubState.GUARD,
            PatrolSubState.GUARD_TIMER,
            PatrolSubState.GUARD_VIEW_TRACK,
        ):
            return vs.guard_view_track_enabled
        if hint in ("patrol", "track") or sub in (PatrolSubState.PATROL, PatrolSubState.TRACK):
            return vs.patrol_track_enabled
        return False

    def on_security_person_event(self, data: dict) -> None:
        if not self._person_alert_allowed(data):
            return
        from patrol_mode.alert_capture import capture_alert_from_person_event

        capture_alert_from_person_event(data)
        get_track_coordinator().on_security_person_event(self, data)

    def on_patrol_track_status(self, data: dict) -> None:
        get_track_coordinator().on_patrol_track_status(self, data)

    def on_guard_status(self, data: dict) -> None:
        if not self.security_active():
            return
        phase = str(data.get("guard_phase", "") or "").strip().lower()
        if phase not in {p.value for p in GuardPhase}:
            return
        self._state.guard_phase = phase
        if phase == GuardPhase.VIEW_TRACKING.value:
            self._state.sub_state = PatrolSubState.GUARD_VIEW_TRACK
            self._state.message = "驻守·视角跟踪中（不可手动转向）"
            get_track_coordinator().publish_motion_for_substate(PatrolSubState.GUARD_VIEW_TRACK)
        elif self._state.sub_state == PatrolSubState.GUARD_VIEW_TRACK:
            self._state.sub_state = PatrolSubState.GUARD
            get_track_coordinator().publish_motion_for_substate(PatrolSubState.GUARD)
            if phase == GuardPhase.MANUAL_ROTATING.value:
                self._state.message = "驻守·手动转向中"
            else:
                self._state.message = "驻守待命（可手动控向）"
        elif phase == GuardPhase.MANUAL_ROTATING.value:
            delta = data.get("delta_deg")
            if delta is not None:
                self._state.message = f"驻守·手动转向 {delta}°"
            else:
                self._state.message = "驻守·手动转向中"

    def _guard_states(self) -> tuple[PatrolSubState, ...]:
        return (PatrolSubState.GUARD, PatrolSubState.GUARD_TIMER, PatrolSubState.GUARD_VIEW_TRACK)

    def guard_rotate(self, delta_deg: float) -> tuple[bool, str, dict[str, Any]]:
        if not self.security_active():
            return False, "未在巡逻模式", self.status_dict()
        if self._state.sub_state not in self._guard_states():
            return False, f"当前子状态 {self._state.sub_state.value} 不可手动转向", self.status_dict()
        if self._state.sub_state == PatrolSubState.GUARD_VIEW_TRACK:
            return False, "视角跟踪中，请先让人离开视野", self.status_dict()
        if abs(delta_deg) > 180:
            return False, "单次转向角度须在 ±180° 内", self.status_dict()
        if mock_vehicle_enabled():
            self._state.guard_phase = GuardPhase.MANUAL_ROTATING.value
            self._state.message = f"驻守·手动转向 {delta_deg}°（mock）"
            return True, "mock 已接受转向命令", self.status_dict()
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if not bridge_enabled():
                return False, "MQTT 未连接，无法下发转向", self.status_dict()
            get_bridge().publish_guard_rotate(delta_deg)
        except Exception as e:
            return False, f"下发转向失败: {e}", self.status_dict()
        self._state.guard_phase = GuardPhase.MANUAL_ROTATING.value
        self._state.message = f"驻守·手动转向 {delta_deg}°"
        return True, "转向命令已下发", self.status_dict()

    def guard_rotate_cancel(self) -> tuple[bool, str, dict[str, Any]]:
        if not self.security_active():
            return False, "未在巡逻模式", self.status_dict()
        if self._state.sub_state not in self._guard_states():
            return False, "当前不在驻守状态", self.status_dict()
        if mock_vehicle_enabled():
            self._state.guard_phase = GuardPhase.IDLE.value
            self._state.message = "驻守待命（可手动控向）"
            return True, "mock 已取消转向", self.status_dict()
        try:
            from mqtt_robot_bridge import bridge_enabled, get_bridge

            if bridge_enabled():
                get_bridge().publish_guard_rotate_cancel()
        except Exception:
            pass
        self._state.guard_phase = GuardPhase.IDLE.value
        self._state.message = "驻守待命（可手动控向）"
        return True, "已取消转向", self.status_dict()

    def _tick_scheduler_off(self) -> None:
        if self._state.manual_block_auto_enter:
            return
        sched = active_schedule()
        if not sched:
            return
        first = FirstMode.PATROL.value if sched.mode == "patrol" else FirstMode.GUARD.value
        self.enter(first_mode=first, via="schedule")

    def _tick_scheduler_on(self) -> None:
        sched = active_schedule()
        if self._state.entered_via == "schedule" and sched is None:
            self.exit(via="schedule")
            return
        if self._state.manual_enter_until_end and sched is None:
            self.exit(via="schedule")

    def _tick_sub_state(self) -> None:
        st = self._state.sub_state
        if st == PatrolSubState.RETURN_PREP:
            self._tick_return_prep()
        elif st == PatrolSubState.WAIT_TASK:
            self._tick_wait_task()
        elif st == PatrolSubState.GUARD_TIMER:
            self._tick_guard_timer()
        elif st == PatrolSubState.PATROL:
            get_patrol_executor().tick()
        elif st == PatrolSubState.TRACK:
            pass
        elif st == PatrolSubState.END_RETURN:
            self._tick_end_return()

    def _tick_return_prep(self) -> None:
        if mock_vehicle_enabled():
            if (
                self._state.mock_nav_deadline_mono is not None
                and time.monotonic() >= self._state.mock_nav_deadline_mono
            ):
                self._state.mock_nav_deadline_mono = None
                self._enter_guard_or_patrol_after_home()
            return
        if robot_at_home():
            self._enter_guard_or_patrol_after_home()

    def _tick_end_return(self) -> None:
        if mock_vehicle_enabled():
            if (
                self._state.mock_nav_deadline_mono is not None
                and time.monotonic() >= self._state.mock_nav_deadline_mono
            ):
                self._state.mock_nav_deadline_mono = None
                task = self._state.task
                if task.patrol_rounds <= 0 or self._state.rounds_done >= max(1, task.patrol_rounds):
                    self._state.sub_state = PatrolSubState.GUARD
                    self._state.guard_phase = GuardPhase.IDLE.value
                    self._state.message = "已回起点，驻守（本轮巡逻结束）"
                    get_track_coordinator().publish_motion_for_substate(PatrolSubState.GUARD)
                else:
                    self._state.sub_state = PatrolSubState.GUARD
                    self._state.guard_phase = GuardPhase.IDLE.value
                    self._state.message = "已回起点（P1d 轮间计时待接）"
                    get_track_coordinator().publish_motion_for_substate(PatrolSubState.GUARD)
            return
        if robot_at_home():
            self._state.sub_state = PatrolSubState.GUARD
            self._state.guard_phase = GuardPhase.IDLE.value
            self._state.message = "已回起点，驻守"
            get_track_coordinator().publish_motion_for_substate(PatrolSubState.GUARD)

    def _tick_wait_task(self) -> None:
        if self._state.wait_task_deadline_mono is None:
            return
        if time.monotonic() < self._state.wait_task_deadline_mono:
            return
        self._state.wait_task_deadline_mono = None
        self._state.sub_state = PatrolSubState.RETURN_PREP
        self._state.message = "等待任务超时，返回起点准备驻守"
        nav_to_entry("wait_task_timeout")
        if mock_vehicle_enabled():
            self._state.mock_nav_deadline_mono = time.monotonic() + MOCK_NAV_SEC

    def _tick_guard_timer(self) -> None:
        if self._state.guard_timer_deadline_mono is None:
            return
        if time.monotonic() < self._state.guard_timer_deadline_mono:
            return
        self._state.guard_timer_deadline_mono = None
        self._state.sub_state = PatrolSubState.PATROL
        self._state.message = "轮间驻守结束，进入巡逻"
        get_track_coordinator().publish_motion_for_substate(PatrolSubState.PATROL)

    def dev_mock_at_home(self) -> tuple[bool, str]:
        if not self.security_active():
            return False, "未在巡逻模式"
        if self._state.sub_state in (PatrolSubState.GUARD, PatrolSubState.PATROL, PatrolSubState.GUARD_VIEW_TRACK):
            return True, f"已在 {self._state.sub_state.value}（mock 自动完成或无需重复操作）"
        if self._state.sub_state != PatrolSubState.RETURN_PREP:
            return False, f"当前子状态为 {self._state.sub_state.value}，仅 return_prep 可 mock 到起点"
        self._state.mock_nav_deadline_mono = None
        self._enter_guard_or_patrol_after_home()
        return True, f"已进入 {self._state.sub_state.value}"

    def cancel_for_switch(self) -> tuple[bool, str]:
        if not self.security_active():
            return False, "未在巡逻模式"
        self._state.sub_state = PatrolSubState.WAIT_TASK
        self._state.message = "已取消当前任务，等待新任务下发"
        self._state.wait_task_deadline_mono = time.monotonic() + WAIT_TASK_TIMEOUT_SEC
        if not mock_vehicle_enabled():
            try:
                from mqtt_robot_bridge import bridge_enabled, get_bridge

                if bridge_enabled():
                    get_bridge().publish_nav_cancel_legacy("patrol_switch")
            except Exception:
                pass
        return True, "已进入 wait_task"

    def update_task_config(self, cfg: PatrolTaskConfig) -> None:
        self._state.task = cfg
        save_task_config(cfg)
