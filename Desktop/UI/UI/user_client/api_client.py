"""
HTTP 封装：桌面与 Android 共用；Base URL 优先读环境变量 PICKUP_API_BASE。
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests

DEFAULT_BASE = os.environ.get("PICKUP_API_BASE", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = 30


def parse_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        d = data.get("detail")
        if isinstance(d, list):
            parts: list[str] = []
            for item in d:
                if isinstance(item, dict) and "msg" in item:
                    parts.append(str(item["msg"]))
                else:
                    parts.append(str(item))
            return "; ".join(parts) if parts else resp.text
        if d is not None:
            return str(d)
    except Exception:
        pass
    return resp.text or f"HTTP {resp.status_code}"


def _json_headers(token: Optional[str]) -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def api_register(base: str, username: str, login_password: str) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/auth/register",
        headers=_json_headers(None),
        json={"username": username.strip(), "login_password": login_password},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_login(base: str, username: str, login_password: str) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/auth/login",
        headers=_json_headers(None),
        json={"username": username.strip(), "login_password": login_password},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_pickup_request(base: str, token: str, door_plate: str) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/pickup/request",
        headers=_json_headers(token),
        json={"door_plate": door_plate.strip()},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_pickup_verify(
    base: str, token: str, task_id: str, login_password: str
) -> dict[str, Any]:
    r = requests.post(
        f"{base}/api/pickup/verify",
        headers=_json_headers(token),
        json={"task_id": task_id.strip(), "login_password": login_password},
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_user_tasks(base: str, token: str) -> list[dict[str, Any]]:
    r = requests.get(
        f"{base}/api/user/tasks", headers=_json_headers(token), timeout=TIMEOUT
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return list(r.json().get("tasks") or [])


def api_robot_state(base: str) -> dict[str, Any]:
    r = requests.get(f"{base}/api/robot/state", timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return r.json()


def api_notifications(base: str, token: str) -> list[dict[str, Any]]:
    r = requests.get(
        f"{base}/api/user/notifications",
        headers=_json_headers(token),
        timeout=TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(parse_error(r))
    return list(r.json().get("items") or [])
