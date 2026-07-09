# -*- coding: utf-8 -*-
"""HTTP client for backend tour API (P3: network-first)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional


def _base_url() -> str:
    return (
        os.environ.get("VOICE_TOUR_API_BASE")
        or os.environ.get("COURIER_API_BASE")
        or os.environ.get("PICKUP_API_BASE")
        or "http://127.0.0.1:8000"
    ).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.environ.get("VOICE_TOUR_API_TIMEOUT", "4"))
    except ValueError:
        return 4.0


class TourApiClient:
    def __init__(self, base: str = "") -> None:
        self.base = (base or _base_url()).rstrip("/")
        self._reachable_cache = False
        self._reachable_ts = 0.0

    @classmethod
    def from_env(cls) -> TourApiClient:
        return cls()

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

    def reachable(self, *, force: bool = False) -> bool:
        if os.environ.get("VOICE_TOUR_API_ENABLED", "1").strip().lower() in (
            "0",
            "false",
            "no",
        ):
            return False
        ttl = float(os.environ.get("VOICE_TOUR_API_PROBE_SEC", "8"))
        now = time.monotonic()
        if not force and now - self._reachable_ts < ttl:
            return self._reachable_cache
        try:
            self._request("GET", "/api/health")
            self._reachable_cache = True
        except Exception:
            self._reachable_cache = False
        self._reachable_ts = now
        return self._reachable_cache

    def tour_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/tour/status")

    def robot_state(self) -> dict[str, Any]:
        return self._request("GET", "/api/robot/state")

    def voice_wake(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/wake")

    def voice_touch(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/touch")

    def voice_discard(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/discard")

    def voice_utterance(self, *, intent: str, room: str = "", text: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/tour/voice/utterance",
            {"intent": intent, "room": room, "text": text},
        )

    def voice_pending_room(self, room: str) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/pending-room", {"room": room})

    def start_tour(self, room: str, *, discard_voice: bool = True) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/tour/start",
            {"room": room, "discard_voice": discard_voice},
        )

    def stop_holding(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/nav/stop-holding")

    def holding_cancel(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/holding/cancel")

    def seize_delivery_return(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/seize-delivery-return")

    def ptt_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/tour/voice/ptt/status")

    def ptt_tap(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/ptt/tap")

    def ptt_awake_sync(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/ptt/awake-sync")

    def ptt_sleep(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/ptt/sleep")

    def ptt_begin(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/ptt/begin")

    def ptt_end(self) -> dict[str, Any]:
        return self._request("POST", "/api/tour/voice/ptt/end")

    def ptt_set_partial(self, text: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/tour/voice/ptt/partial",
            {"text": text},
        )

    def ptt_set_final(self, text: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/tour/voice/ptt/final",
            {"text": text},
        )

    @staticmethod
    def parse_error(exc: BaseException) -> str:
        if isinstance(exc, urllib.error.HTTPError):
            try:
                body = exc.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                detail = data.get("detail")
                if detail:
                    return str(detail)
            except Exception:
                pass
            return f"HTTP {exc.code}"
        return str(exc)
