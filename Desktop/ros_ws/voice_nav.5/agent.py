# -*- coding: utf-8 -*-
"""Voice nav agent: QA / room navigation (no chassis motion control)."""
from __future__ import annotations

import os
from typing import Any, Optional

from . import audio_cache, backend_router, kb_qa, llm_intent, loader, nav_guard, retriever, tts, wake
from .mission_state import MissionPhase, NavSession
from .stt_filter import is_incomplete_fragment, is_new_question, normalize_spoken_text
from .nav_bridge import NavBridge
from .session import Session

# Chinese keyword lists (unicode escapes for cross-platform file encoding)
NAV_KEYS = (
    "\u5e26\u6211\u53bb", "\u5bfc\u822a", "\u9001\u6211\u53bb", "\u6211\u8981\u53bb",
    "\u524d\u5f80", "\u9886\u6211\u53bb", "\u9001\u6211\u5230", "\u5e26\u6211\u5230",
)
QA_KEYS = (
    "\u5728\u54ea", "\u5728\u54ea\u91cc", "\u662f\u4ec0\u4e48", "\u5e72\u4ec0\u4e48",
    "\u4ecb\u7ecd", "\u6709\u54ea\u4e9b", "\u6709\u4ec0\u4e48", "\u54ea\u4e2a",
    "\u4e86\u89e3", "\u54a8\u8be2", "\u529e\u4ec0\u4e48", "\u627e\u54ea", "\u627e\u8c01",
    "\u8d1f\u8d23", "\u529f\u80fd", "\u7528\u9014", "\u600e\u4e48\u8d70", "\u5982\u4f55",
    "\u884c\u674e", "\u5bc4\u5b58", "\u5b58\u653e", "\u653e\u5728\u54ea", "\u8be5\u53bb",
    "\u5e94\u8be5", "\u521a\u8fdb", "\u53bb\u54ea", "\u53bb\u54ea\u91cc",
)
CANCEL_NAV_KEYS = (
    "\u53d6\u6d88\u5bfc\u822a", "\u505c\u6b62\u5bfc\u822a",
    "\u4e0d\u8981\u5bfc\u822a", "\u4e0d\u53bb\u4e86",
)
END_SESSION_KEYS = ("\u9000\u4e0b", "\u518d\u89c1", "\u4f11\u606f\u5427", "\u4f11\u606f")
FLOOR_LIST_KEYS = (
    "\u4e8c\u697c\u6709\u4ec0\u4e48", "\u4e00\u697c\u6709\u4ec0\u4e48",
    "2\u697c\u6709\u4ec0\u4e48", "1\u697c\u6709\u4ec0\u4e48",
    "\u90fd\u6709\u4ec0\u4e48\u623f\u95f4",
    "\u6709\u4ec0\u4e48\u623f\u95f4", "\u6709\u54ea\u4e9b\u623f\u95f4",
)
class VoiceNavAgent:
    def __init__(self) -> None:
        self.kb = loader.load_knowledge()
        self.nav = NavBridge()
        self.session = Session()
        self._last_nav_state = ""
        self._processing = False
        self.nav_session = NavSession()
        self.nav.set_status_callback(self._on_mqtt_status)

    def is_busy(self) -> bool:
        return self._processing or tts.is_busy()

    def mission_active(self) -> bool:
        if self.nav_session.mission_active():
            return True
        phase = self.nav_session.phase
        if phase in (MissionPhase.ARRIVED, MissionPhase.CANCELLED, MissionPhase.FAILED):
            return False
        return nav_guard.is_active_mission(self.nav.nav_state)

    def mark_nav_started(self, room_id: str = "") -> None:
        self.nav_session.mark_nav_started(room_id)

    def mark_nav_ended(self, phase: MissionPhase = MissionPhase.IDLE) -> None:
        self.nav_session.mark_nav_ended(phase)

    def _speak_status_once(self, cache_key: str, fallback: str) -> None:
        if not self.nav_session.should_speak_status(cache_key):
            return
        tts.speak_key(cache_key, fallback_text=fallback, tier="status")

    def handle_wake_stop_mission(self) -> None:
        """导览途中：唤醒词截停导航任务。"""
        self._processing = True
        try:
            self.session.clear_pending()
            self.mark_nav_ended(MissionPhase.CANCELLED)
            tts.stop()
            self._cancel_navigation_task(reason="wake_stop")
            tts.speak_key("fixed:wake_stop", fallback_text=wake.wake_stop_reply(), tier="status")
            print("[wake_stop] mission stopped via wake word", flush=True)
        finally:
            self._processing = False

    def handle_end_session(self) -> None:
        self.session.clear_pending()
        tts.stop()
        tts.speak_key(
            "fixed:session_bye",
            fallback_text="\u597d\u7684\uff0c\u6709\u9700\u8981\u518d\u53eb\u6211",
            tier="status",
        )

    def _cancel_navigation_task(self, reason: str = "voice") -> tuple[bool, str]:
        ok, msg = self.nav.cancel_navigation(reason=reason)
        print(f"[\u5bfc\u822a\u53d6\u6d88] {msg}", flush=True)
        return ok, msg

    def _handle_cancel_navigation(self) -> bool:
        self.session.clear_pending()
        self.mark_nav_ended(MissionPhase.CANCELLED)
        self.nav_session.on_nav_cancel()
        tts.stop()
        self._cancel_navigation_task(reason="voice_cancel")
        tts.speak_key("fixed:nav_cancel", fallback_text="\u5df2\u53d6\u6d88\u5bfc\u822a", tier="status")
        return True

    def _on_mqtt_status(self, payload: dict) -> None:
        mt = str(payload.get("msg_type", ""))
        if mt == "robot_heartbeat":
            st = str(payload.get("nav_state", ""))
            if not st:
                return
            nav_epoch_raw = payload.get("nav_epoch")
            goal_room = str(payload.get("current_goal_room", "")).strip()
            if nav_epoch_raw is not None:
                try:
                    self.nav_session.confirm_nav_epoch(int(nav_epoch_raw), goal_room)
                except (TypeError, ValueError):
                    pass
            prev, cur, became_idle = self.nav_session.on_heartbeat(st)
            if st != self._last_nav_state:
                self._last_nav_state = st
                if became_idle:
                    print("[nav] heartbeat -> IDLE, mission ended", flush=True)
                labels = {
                    "GOING_TO_ELEVATOR": "\u6b63\u5728\u524d\u5f80\u7535\u68af",
                    "WAITING_ELEVATOR": "\u6b63\u5728\u7b49\u5f85\u7535\u68af",
                    "SWITCHING_MAP": "\u6b63\u5728\u5207\u6362\u697c\u5c42\u5730\u56fe",
                    "NAVIGATING_TO_ROOM": "\u6b63\u5728\u524d\u5f80\u76ee\u6807\u623f\u95f4",
                    "IDLE": "\u5bfc\u822a\u7a7a\u95f2",
                }
                key = audio_cache.status_key(st)
                if key and prev != cur:
                    self._speak_status_once(key, labels.get(st, ""))
        elif mt == "tour_arrived":
            room = str(payload.get("room", "")).strip()
            nav_epoch_raw = payload.get("nav_epoch")
            nav_epoch: Optional[int] = None
            if nav_epoch_raw is not None:
                try:
                    nav_epoch = int(nav_epoch_raw)
                except (TypeError, ValueError):
                    nav_epoch = None
            rid = self.nav_session.on_tour_arrived(room, nav_epoch)
            if not rid:
                print(
                    f"[nav] tour_arrived ignored room={room} epoch={nav_epoch} "
                    f"expected={self.nav_session.mission_nav_epoch}",
                    flush=True,
                )
                return
            key = audio_cache.room_arrived_key(rid)
            room_obj = loader.get_room_by_id(self.kb, rid)
            name = str(room_obj.get("name", rid)) if room_obj else rid
            self._speak_status_once(key, f"\u5df2\u5230\u8fbe{name}")
            print(f"[nav] tour_arrived room={room} epoch={nav_epoch}", flush=True)
        elif mt == "nav_cancel_result":
            self.nav_session.on_nav_cancel()
            self.mark_nav_ended(MissionPhase.CANCELLED)

    def _start_navigation(self, room_id: str, *, floor_hint: str = "") -> bool:
        room = loader.get_room_by_id(self.kb, room_id)
        if room is None:
            tts.speak(f"\u672a\u627e\u5230\u623f\u95f4{room_id}\u7684\u8d44\u6599")
            return False
        ok, msg = self.nav.navigate_room(room_id)
        print(f"[\u5bfc\u822a] {msg}", flush=True)
        if not ok:
            tts.speak(msg)
            return False
        self.mark_nav_started(room_id)
        name = str(room.get("name", room_id))
        fb = f"{floor_hint}\u6b63\u5728\u5e26\u60a8\u53bb{name}" if floor_hint else f"\u6b63\u5728\u5e26\u60a8\u53bb{name}"
        key = audio_cache.room_nav_start_key(room_id)
        self._speak_status_once(key, fb)
        return True

    def _kb_snippets(self, hits: list[tuple[dict, float]]) -> str:
        lines = []
        for room, score in hits:
            lines.append(
                f"- {room.get('id')}/{room.get('name')}/{room.get('floor')}: "
                f"{room.get('intro_short', '')} (score={score:.0f})"
            )
        return "\n".join(lines) if lines else "(\u65e0\u5339\u914d)"

    def _resolve_room(self, text: str, hits: list[tuple[dict, float]]) -> Optional[str]:
        rid = retriever.extract_room_id_from_text(text)
        if rid:
            return rid
        if hits:
            top_room, top_score = hits[0]
            if len(hits) == 1 or top_score >= hits[1][1] + 3:
                return str(top_room.get("id", "")) or None
        return None

    def _classify(self, text: str) -> str:
        t = text.strip()
        if any(k in t for k in CANCEL_NAV_KEYS):
            return "cancel"
        if any(k in t for k in FLOOR_LIST_KEYS):
            return "floor_list"
        has_nav = retriever.is_explicit_nav_request(t) or any(k in t for k in NAV_KEYS)
        has_qa = any(k in t for k in QA_KEYS) or retriever.looks_like_question(t)
        if has_nav and has_qa:
            return "qa_then_navigate" if retriever.is_explicit_nav_request(t) else "qa"
        if has_nav:
            return "navigate"
        if has_qa:
            return "qa"
        rid = retriever.extract_room_id_from_text(t)
        if rid and not has_qa and retriever.is_explicit_nav_request(t):
            return "navigate"
        hits = retriever.search_rooms(self.kb, t, top_k=1)
        if hits and hits[0][1] >= 6:
            return "qa"
        return "unknown"

    def _intro_for_room(self, room: dict) -> str:
        return kb_qa.format_intro(room, current_floor=self.nav.current_floor)

    def _confirm_nav_prompt(self) -> str:
        return "\u9700\u8981\u5e26\u60a8\u8fc7\u53bb\u5417\uff1f\u8bf7\u8bf4\u597d\u7684\u6216\u4e0d\u7528\u3002"

    def _llm_reply_text(self, llm_obj: dict[str, Any] | None) -> Optional[str]:
        if not llm_obj or not llm_obj.get("reply"):
            return None
        if not llm_intent.llm_intent_enabled():
            return None
        gate = os.environ.get("VOICE_NAV_LLM_REPLY", "1").strip().lower()
        if gate in ("0", "false", "no"):
            return None
        reply = str(llm_obj["reply"]).strip()
        return reply or None

    def _execute_room_intent(
        self,
        *,
        text: str,
        intent: str,
        room_id: str,
        intro: str,
        hits: list[tuple[dict, float]],
        warmed: bool,
    ) -> bool:
        room = loader.get_room_by_id(self.kb, room_id)
        if room is None:
            tts.speak(f"\u672a\u627e\u5230\u623f\u95f4{room_id}\u7684\u8d44\u6599")
            return warmed

        floor = str(room.get("floor", ""))
        cur = self.nav.current_floor

        if intent == "navigate" and retriever.is_explicit_nav_request(text):
            extra = ""
            if cur != "?" and floor and cur != floor:
                extra = f"\u60a8\u5f53\u524d\u5728{cur}\uff0c{room.get('name')}\u5728{floor}\uff0c"
            self._start_navigation(room_id, floor_hint=extra)
            return warmed

        if intent in ("qa", "qa_then_navigate", "navigate", "unknown"):
            tts.speak(intro)
            print(f"[\u95ee\u7b54] {intro}", flush=True)
            if room.get("navigable", True):
                self.session.set_pending_nav(str(room.get("id", "")), "")
                tts.speak(self._confirm_nav_prompt())
            elif hits and len(hits) > 1 and hits[0][1] - hits[1][1] < 3:
                opts = "\u3001".join(str(h[0].get("name")) for h in hits[:3])
                tts.speak(f"\u60a8\u662f\u6307{opts}\u4e2d\u7684\u54ea\u4e00\u4e2a\uff1f")
            return warmed
        return warmed

    def _handle_llm_kb(
        self,
        text: str,
        *,
        host: str,
        path: str,
        warmed: bool,
    ) -> bool:
        hits = retriever.search_rooms(self.kb, text, top_k=3)
        llm_obj = backend_router.parse_intent_with_llm(
            text,
            self._kb_snippets(hits),
            self.nav.current_floor,
            host,
            path,
            intent="unknown",
            hits=hits,
            kb=self.kb,
        )
        if not llm_obj or not llm_obj.get("intent"):
            print("[LLM] \u65e0\u6709\u6548\u7ed3\u679c\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5", flush=True)
            tts.speak("\u62b1\u6b49\uff0c\u5927\u6a21\u578b\u6682\u65f6\u65e0\u6cd5\u56de\u7b54\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002")
            return warmed

        intent = str(llm_obj.get("intent", "qa"))
        if intent in ("navigate", "qa_then_navigate") and not retriever.is_explicit_nav_request(text):
            intent = "qa"
        llm_reply = self._llm_reply_text(llm_obj)

        if intent == "cancel":
            self._handle_cancel_navigation()
            return warmed

        if intent == "clarify":
            if llm_reply:
                tts.speak(llm_reply)
                print(f"[\u95ee\u7b54] {llm_reply}", flush=True)
            else:
                tts.speak(
                    "\u8bf7\u518d\u8bf4\u4e00\u904d\uff0c\u4f8b\u5982\uff1a\u8d22\u52a1\u5728\u54ea\u3001"
                    "\u884c\u674e\u653e\u5728\u54ea\u3001\u6216\u5e26\u6211\u53bb201\u3002"
                )
            return warmed

        if intent == "floor_list":
            rooms = loader.list_navigable_rooms(self.kb)
            floor_filter = None
            if "\u4e09\u697c" in text or "3\u697c" in text:
                floor_filter = "3F"
            elif "2" in text or "\u4e8c" in text:
                floor_filter = "2F"
            elif "1" in text or "\u4e00" in text:
                floor_filter = "1F"
            reply = llm_reply or kb_qa.format_floor_list(rooms, floor_filter=floor_filter)
            tts.speak(reply)
            print(f"[\u95ee\u7b54] {reply}", flush=True)
            return warmed

        room_id = str(llm_obj["room_id"]).strip() if llm_obj.get("room_id") else None
        if room_id and intent in ("qa", "qa_then_navigate", "navigate", "unknown"):
            intro = llm_reply or self._intro_for_room(loader.get_room_by_id(self.kb, room_id) or {})
            return self._execute_room_intent(
                text=text,
                intent=intent,
                room_id=room_id,
                intro=intro,
                hits=hits,
                warmed=warmed,
            )

        if llm_reply:
            tts.speak(llm_reply)
            print(f"[\u95ee\u7b54] {llm_reply}", flush=True)
            if llm_obj.get("room_id"):
                rid = str(llm_obj["room_id"]).strip()
                room = loader.get_room_by_id(self.kb, rid)
                if room and room.get("navigable", True) and intent in ("qa", "qa_then_navigate"):
                    self.session.set_pending_nav(rid, "")
                    tts.speak(self._confirm_nav_prompt())
            return warmed

        tts.speak("\u62b1\u6b49\uff0c\u6682\u65f6\u65e0\u6cd5\u7406\u89e3\u60a8\u7684\u95ee\u9898\uff0c\u8bf7\u6362\u4e2a\u65b9\u5f0f\u518d\u95ee\u3002")
        print("[LLM] \u672a\u8fd4\u56de\u53ef\u64ad\u62a5\u5185\u5bb9", flush=True)
        return warmed

    def _handle_rules_kb(self, text: str, *, warmed: bool) -> bool:
        intent = self._classify(text)
        hits = retriever.search_rooms(self.kb, text, top_k=3)

        if intent == "cancel":
            self._handle_cancel_navigation()
            return warmed

        if intent == "floor_list":
            rooms = loader.list_navigable_rooms(self.kb)
            floor_filter = None
            if "\u4e09\u697c" in text or "3\u697c" in text:
                floor_filter = "3F"
            elif "2" in text or "\u4e8c" in text:
                floor_filter = "2F"
            elif "1" in text or "\u4e00" in text:
                floor_filter = "1F"
            reply = kb_qa.format_floor_list(rooms, floor_filter=floor_filter)
            tts.speak(reply)
            print(f"[\u95ee\u7b54] {reply}", flush=True)
            return warmed

        room_id = self._resolve_room(text, hits)
        if intent in ("qa", "qa_then_navigate", "navigate", "unknown") and room_id:
            intro = self._intro_for_room(loader.get_room_by_id(self.kb, room_id) or {})
            return self._execute_room_intent(
                text=text,
                intent=intent,
                room_id=room_id,
                intro=intro,
                hits=hits,
                warmed=warmed,
            )

        if hits:
            room, top_score = hits[0]
            if top_score < 4:
                print(
                    "[\u63d0\u793a] \u672a\u8bc6\u522b\u610f\u56fe\u3002"
                    "\u53ef\u8bf4\uff1a\u8d22\u52a1\u5728\u54ea / \u53bb201 / \u4e00\u697c\u6709\u4ec0\u4e48\u623f\u95f4",
                    flush=True,
                )
                return warmed
            intro = self._intro_for_room(room)
            tts.speak(intro)
            print(f"[\u95ee\u7b54] {intro}", flush=True)
            if room.get("navigable", True):
                self.session.set_pending_nav(str(room.get("id", "")), "")
                tts.speak(self._confirm_nav_prompt())
            return warmed

        tts.speak(
            "\u6682\u672a\u627e\u5230\u76f8\u5173\u623f\u95f4\u3002"
            "\u60a8\u53ef\u4ee5\u8bf4\uff1a\u8d22\u52a1\u5728\u54ea\u3001\u53bb201\u3001\u6216\u4e00\u697c\u6709\u4ec0\u4e48\u623f\u95f4\u3002"
        )
        print("[\u63d0\u793a] \u672a\u8bc6\u522b\u610f\u56fe", flush=True)
        return warmed

    def handle_text(
        self,
        text: str,
        *,
        host: str,
        path: str,
        warmed: bool,
    ) -> tuple[bool, str]:
        self._processing = True
        try:
            return self._handle_text_inner(
                text, host=host, path=path, warmed=warmed,
            )
        finally:
            self._processing = False

    def _handle_text_inner(
        self,
        text: str,
        *,
        host: str,
        path: str,
        warmed: bool,
    ) -> tuple[bool, str]:
        text = normalize_spoken_text((text or "").strip())
        if not text:
            return warmed, ""
        ptt_mode = os.environ.get("VOICE_INPUT_MODE", "enter").strip().lower() in (
            "enter",
            "terminal",
            "",
        )
        if not ptt_mode and is_incomplete_fragment(text):
            print(
                "[\u7b49\u5f85] \u8bed\u53e5\u4e0d\u5b8c\u6574\uff0c"
                "\u8bf7\u8bf4\u5b8c\u6574\u53e5\u5982\uff1a\u4e00\u697c\u6709\u4ec0\u4e48\u623f\u95f4",
                flush=True,
            )
            return warmed, ""

        if self.mission_active():
            print("[\u5bfc\u89c8] \u5bfc\u822a\u4e2d\uff0c\u8bf7\u7528\u5524\u9192\u8bcd\u622a\u505c", flush=True)
            return warmed, ""

        if self.session.pending_room_id:
            if self.session.is_confirm_yes(text):
                rid = self.session.pending_room_id
                self.session.clear_pending()
                self._start_navigation(str(rid))
                return warmed, ""
            if self.session.is_confirm_no(text):
                self.session.clear_pending()
                tts.speak_key("fixed:confirm_no", fallback_text="\u597d\u7684\uff0c\u5df2\u53d6\u6d88", tier="status")
                return warmed, ""
            if is_new_question(text):
                self.session.clear_pending()
            else:
                print(
                    "[\u7b49\u5f85] \u8bf7\u5148\u56de\u7b54\u300c\u597d\u7684\u300d\u6216\u300c\u4e0d\u7528\u300d",
                    flush=True,
                )
                return warmed, ""

        if any(k in text for k in CANCEL_NAV_KEYS):
            self._handle_cancel_navigation()
            return warmed, ""

        if any(k in text for k in END_SESSION_KEYS):
            self.handle_end_session()
            return warmed, "end_session"

        if llm_intent.llm_only_answers():
            if backend_router.resolve_backend() == "rules":
                print("[\u6a21\u5f0f] \u5927\u6a21\u578b\u4e0d\u53ef\u7528\uff0c\u964d\u7ea7\u89c4\u5219+\u77e5\u8bc6\u5e93", flush=True)
                warmed = self._handle_rules_kb(text, warmed=warmed)
                return warmed, ""
            print(f"[\u6a21\u5f0f] \u5927\u6a21\u578b+\u77e5\u8bc6\u5e93 ({backend_router.backend_mode_label()})", flush=True)
            warmed = self._handle_llm_kb(text, host=host, path=path, warmed=warmed)
            return warmed, ""

        print("[\u6a21\u5f0f] \u89c4\u5219+\u77e5\u8bc6\u5e93", flush=True)
        warmed = self._handle_rules_kb(text, warmed=warmed)
        return warmed, ""

    def close(self) -> None:
        self.nav.stop()
