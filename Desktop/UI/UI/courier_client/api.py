"""
送货员端 HTTP：与 backend `task_manager` 行为一致，仅走 /api/courier/*。
Base URL：环境变量 COURIER_API_BASE，否则 PICKUP_API_BASE，否则本机 8000。
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE = (
    os.environ.get("COURIER_API_BASE")
    or os.environ.get("PICKUP_API_BASE")
    or "http://127.0.0.1:8000"
).rstrip("/")
TIMEOUT = 30


def parse_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        d = data.get("detail")
        if isinstance(d, list):
            parts: list[str] = []
            for x in d:
                if isinstance(x, dict) and "msg" in x:
                    parts.append(str(x["msg"]))
                else:
                    parts.append(str(x))
            return "; ".join(parts) if parts else resp.text
        if d is not None:
            return str(d)
    except Exception:
        pass
    return resp.text or f"HTTP {resp.status_code}"


def api_queue(base: str) -> list[dict[str, Any]]:
    r = requests.get(f"{base}/api/courier/queue", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return list(r.json().get("tasks") or [])


def api_robot_state(base: str) -> dict[str, Any]:
    r = requests.get(f"{base}/api/robot/state", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_confirm(base: str, match_key: str) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/courier/confirm",
        headers={"Content-Type": "application/json"},
        json={"match_key": match_key.strip()},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_mark_delivered(base: str, task_id: str) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/courier/mark-delivered/{task_id.strip()}",
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_robot_return_home(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/courier/robot/return-home", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_debug_clear_all_tasks(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/courier/debug/clear-all-tasks", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_building_rooms(base: str) -> dict[str, Any]:
    r = requests.get(f"{base}/api/building/rooms", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_status(base: str) -> dict[str, Any]:
    r = requests.get(f"{base}/api/tour/status", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_start(base: str, room: str, *, discard_voice: bool = True) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/tour/start",
        headers={"Content-Type": "application/json"},
        json={"room": room.strip(), "discard_voice": discard_voice},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_wake(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/wake", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_discard(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/discard", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_holding_cancel(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/holding/cancel", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_simulate_arrived(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/simulate/arrived", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_touch(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/touch", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_ptt_tap(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/ptt/tap", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_ptt_awake_sync(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/ptt/awake-sync", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_ptt_sleep(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/ptt/sleep", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_ptt_status(base: str) -> dict[str, Any]:
    r = requests.get(f"{base}/api/tour/voice/ptt/status", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_ptt_begin(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/ptt/begin", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_ptt_end(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/voice/ptt/end", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_voice_utterance(
    base: str, *, intent: str, room: str = "", text: str = ""
) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/tour/voice/utterance",
        headers={"Content-Type": "application/json"},
        json={"intent": intent, "room": room, "text": text},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_finish(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/finish", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_tour_cancel(base: str) -> dict[str, Any]:
    r = requests.post(f"{base}/api/tour/cancel", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()
