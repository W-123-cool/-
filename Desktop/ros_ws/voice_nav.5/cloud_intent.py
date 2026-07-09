# -*- coding: utf-8 -*-
"""Cloud text LLM (OpenAI-compatible: DashScope / MaaS)."""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from . import llm_intent
from .env_util import norm_env


def cloud_api_key() -> str:
    return norm_env(
        os.environ.get("VOICE_NAV_CLOUD_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or ""
    )


def cloud_configured() -> bool:
    return bool(cloud_api_key()) and bool(cloud_base_url())


def cloud_base_url() -> str:
    url = norm_env(
        os.environ.get("VOICE_NAV_CLOUD_BASE_URL")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or ""
    ).rstrip("/")
    if "{WorkspaceId}" in url:
        ws = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
        if ws:
            url = url.replace("{WorkspaceId}", ws)
    return url


def cloud_model() -> str:
    return norm_env(
        os.environ.get("VOICE_NAV_CLOUD_MODEL")
        or os.environ.get("DASHSCOPE_MODEL")
        or "qwen-max",
        default="qwen-max",
    )


def _cloud_timeout() -> float:
    return float(os.environ.get("VOICE_NAV_CLOUD_TIMEOUT", "15"))


def _cloud_max_tokens() -> int:
    return int(os.environ.get("VOICE_NAV_CLOUD_MAX_TOKENS", "256"))


def _extract_message(body: Any) -> str:
    if isinstance(body, dict):
        choices = body.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"]).strip()
        for key in ("content", "response", "text", "reply"):
            if body.get(key):
                return str(body[key]).strip()
    return ""


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:500]
    except Exception:
        return ""


def probe_auth() -> tuple[str, str]:
    """
    Probe cloud API auth. Returns (status, detail).
    status: ok | invalid_api_key | no_network | misconfigured | error
    """
    api_key = cloud_api_key()
    base = cloud_base_url()
    if not api_key:
        return "misconfigured", "missing DASHSCOPE_API_KEY"
    if not base or "{WorkspaceId}" in base:
        return "misconfigured", "missing or invalid DASHSCOPE_BASE_URL"

    url = base + "/chat/completions"
    payload = {
        "model": cloud_model(),
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    timeout = min(_cloud_timeout(), float(os.environ.get("VOICE_NAV_PROBE_TIMEOUT", "8")))
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return "ok", url
            return "error", f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        detail = _read_http_error_body(exc)
        if exc.code in (401, 403):
            return "invalid_api_key", detail or f"HTTP {exc.code}"
        if exc.code >= 500:
            return "error", detail or f"HTTP {exc.code}"
        return "error", detail or f"HTTP {exc.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        return "no_network", str(exc)
    except json.JSONDecodeError as exc:
        return "error", str(exc)


def chat(prompt: str) -> tuple[Optional[dict[str, Any]], float, str]:
    api_key = cloud_api_key()
    base = cloud_base_url()
    if not api_key:
        return None, 0.0, ""
    if not base or "{WorkspaceId}" in base:
        print(
            "[\u4e91\u7aef] \u672a\u914d\u7f6e base_url\uff0c"
            "\u8bf7\u8bbe\u7f6e DASHSCOPE_BASE_URL \u5e76 export DASHSCOPE_WORKSPACE_ID",
            flush=True,
        )
        return None, 0.0, ""

    url = base + "/chat/completions"
    payload: dict[str, Any] = {
        "model": cloud_model(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": _cloud_max_tokens(),
    }
    temp = os.environ.get("VOICE_NAV_CLOUD_TEMPERATURE", "").strip()
    if temp:
        payload["temperature"] = float(temp)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=_cloud_timeout()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - t0
        detail = _read_http_error_body(exc)
        print(
            f"[\u4e91\u7aef] \u8bf7\u6c42\u5931\u8d25 {elapsed:.1f}s (HTTP {exc.code}: {exc.reason})",
            flush=True,
        )
        if detail:
            print(f"[\u4e91\u7aef] \u54cd\u5e94: {detail}", flush=True)
        if exc.code == 401:
            print(
                "[\u4e91\u7aef] 401 \u901a\u5e38\u662f API Key \u65e0\u6548\u3001Key \u4e0e URL \u5730\u57df\u4e0d\u5339\u914d\u3001"
                "\u6216 Key \u4e0d\u5c5e\u4e8e\u8be5\u4e1a\u52a1\u7a7a\u95f4",
                flush=True,
            )
        return None, elapsed, ""
    except (
        urllib.error.URLError,
        socket.timeout,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        elapsed = time.monotonic() - t0
        print(f"[\u4e91\u7aef] \u8bf7\u6c42\u5931\u8d25 {elapsed:.1f}s ({exc})", flush=True)
        return None, elapsed, ""

    raw = _extract_message(body)
    elapsed = time.monotonic() - t0
    if os.environ.get("VOICE_NAV_LLM_DEBUG", "0").strip().lower() in ("1", "true", "yes"):
        print(f"[\u4e91\u7aef] raw={ (raw or '')[:200] }", flush=True)
    obj = llm_intent.parse_llm_response(raw)
    return obj, elapsed, raw
