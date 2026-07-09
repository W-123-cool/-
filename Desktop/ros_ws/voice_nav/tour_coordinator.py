# -*- coding: utf-8 -*-
"""Tour coordinator: API-first with local MQTT fallback (P3)."""
from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any, Optional

from .tour_api_client import TourApiClient

if TYPE_CHECKING:
    from .nav_bridge import NavBridge


def _api_required() -> bool:
    return os.environ.get("VOICE_TOUR_REQUIRE_API", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class TourCoordinator:
    """有网优先走后端 tour_manager；无网回退 tour_nav / nav_room 本地 MQTT。"""

    def __init__(self, nav: NavBridge) -> None:
        self._nav = nav
        self._api = TourApiClient.from_env()
        self._phase = "idle"
        self._tour_id = ""
        self._room = ""

    def use_api(self) -> bool:
        return self._api.reachable()

    def _api_unavailable_msg(self) -> str:
        base = os.environ.get("VOICE_TOUR_API_BASE") or os.environ.get(
            "COURIER_API_BASE", ""
        )
        hint = f"（{base}）" if base else ""
        return f"导览后端不可达{hint}，请确认 PC backend 已启动且网络可达"

    def _reject_if_api_required(self) -> Optional[str]:
        if _api_required() and not self.use_api():
            return self._api_unavailable_msg()
        return None

    def refresh(self) -> dict[str, Any]:
        if not self.use_api():
            return {}
        try:
            st = self._api.tour_status()
            self._phase = str(st.get("phase", "idle"))
            self._tour_id = str(st.get("tour_id", "") or "")
            self._room = str(st.get("room", "") or "")
            return st
        except Exception:
            return {}

    def api_mission_active(self) -> bool:
        self.refresh()
        return self._phase in ("navigating", "returning")

    def api_holding(self) -> bool:
        self.refresh()
        return self._phase == "holding"

    def on_kws_wake(self) -> tuple[bool, str]:
        err = self._reject_if_api_required()
        if err:
            return False, err
        if not self.use_api():
            return False, "local"
        try:
            self.refresh()
            ph = self._phase
            rs = self._api.robot_state()
            tour_busy = bool((rs.get("tour") or {}).get("tour_busy"))
            if str(rs.get("robot_state", "")) == "returning" and not tour_busy:
                data = self._api.seize_delivery_return()
            else:
                data = self._api.voice_wake()
            self.refresh()
            return True, str(data.get("message", "已唤醒"))
        except Exception as exc:
            return False, TourApiClient.parse_error(exc)

    def on_ptt_begin(self) -> None:
        if self.use_api():
            try:
                self._api.voice_touch()
            except Exception:
                try:
                    self._api.voice_wake()
                except Exception:
                    pass

    def on_wake_stop(self) -> tuple[bool, str]:
        """唤醒词截停：原地待机（与 UI「取消导览返航」不同）。"""
        err = self._reject_if_api_required()
        if err:
            return False, err
        if self.use_api():
            try:
                st = self.refresh()
                ph = self._phase
                if ph in ("navigating", "returning"):
                    data = self._api.stop_holding()
                elif ph == "holding":
                    return True, "已在原地待机"
                else:
                    return True, "无活动导览"
                self.refresh()
                return True, str(data.get("message", "已截停，原地待机"))
            except Exception as exc:
                return False, TourApiClient.parse_error(exc)
        self.refresh()
        tid = self._tour_id.strip()
        if tid:
            ok, msg = self._nav.stop_tour_in_place(tid, reason="wake_stop")
            if ok:
                return ok, msg
        ok, msg = self._nav.cancel_navigation(reason="wake_stop")
        return ok, msg

    def start_navigation(self, room_id: str) -> tuple[bool, str, str]:
        """Returns ok, message, tour_id."""
        room_id = str(room_id).strip()
        if not room_id:
            return False, "empty room", ""

        err = self._reject_if_api_required()
        if err:
            return False, err, ""

        if self.use_api():
            try:
                st = self.refresh()
                ph = self._phase
                active = bool(st.get("active"))
                if not active or ph in ("idle", ""):
                    self._api.voice_wake()
                    self.refresh()
                    ph = self._phase
                elif ph in ("holding", "at_dest", "waiting_voice"):
                    try:
                        self._api.voice_touch()
                    except Exception:
                        pass
                elif ph == "navigating":
                    return False, "已在导览中，请用唤醒词截停", ""
                elif ph == "returning":
                    return False, "正在返回起点，请稍候", ""
                data = self._api.voice_utterance(
                    intent="navigate", room=room_id, text=room_id,
                )
                st = data.get("status") if isinstance(data.get("status"), dict) else self.refresh()
                tid = str(st.get("tour_id", "") or "")
                self._phase = str(st.get("phase", "navigating"))
                self._tour_id = tid
                self._room = room_id
                return True, str(data.get("message", "导览已发车")), tid
            except Exception as exc:
                api_err = TourApiClient.parse_error(exc)
                print(f"[tour-api] navigate failed: {api_err}", flush=True)
                if _api_required():
                    return False, api_err, ""

        if _api_required():
            return False, self._api_unavailable_msg(), ""

        tid = str(uuid.uuid4())
        ok, msg = self._nav.navigate_tour(tid, room_id)
        if not ok:
            ok, msg = self._nav.navigate_room(room_id)
            tid = ""
        else:
            self._tour_id = tid
        self._phase = "navigating" if ok else "idle"
        self._room = room_id if ok else ""
        return ok, msg, self._tour_id

    def cancel_navigation(self) -> tuple[bool, str]:
        err = self._reject_if_api_required()
        if err:
            return False, err
        if self.use_api():
            try:
                st = self.refresh()
                if self._phase == "navigating":
                    data = self._api.stop_holding()
                elif self._phase in ("holding", "at_dest"):
                    data = self._api.holding_cancel()
                elif self._phase == "returning":
                    data = self._api.stop_holding()
                else:
                    return True, "无活动导览"
                self.refresh()
                return True, str(data.get("message", "已取消"))
            except Exception as exc:
                return False, TourApiClient.parse_error(exc)
        if _api_required():
            return False, self._api_unavailable_msg()
        return self._nav.cancel_navigation(reason="voice_cancel")

    def record_pending_room(self, room_id: str) -> tuple[bool, str]:
        if self.use_api():
            try:
                data = self._api.voice_pending_room(room_id)
                return True, str(data.get("message", "ok"))
            except Exception as exc:
                return False, TourApiClient.parse_error(exc)
        return True, "local"

    def report_qa_touch(self) -> None:
        if self.use_api():
            try:
                self._api.voice_touch()
            except Exception:
                pass
