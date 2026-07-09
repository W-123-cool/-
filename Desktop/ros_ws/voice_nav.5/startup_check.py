# -*- coding: utf-8 -*-
"""Startup checks for voice-nav LLM (UI flow / CLI)."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Optional

from . import cloud_intent, llm_intent, net_probe
from .env_util import bootstrap_voice_nav_env, norm_env, normalize_voice_nav_env


@dataclass
class StartupResult:
    ok: bool
    backend: str  # cloud | local | rules
    reason: str  # ok | rules | not_configured | no_network | invalid_api_key | local_down | error
    message: str
    detail: str = ""
    cloud_url: str = ""
    cloud_model: str = ""
    local_host: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def _clean_host_path(host: str, path: str) -> tuple[str, str]:
    host = norm_env(host, default="http://127.0.0.1:8001").rstrip("/")
    path = norm_env(path, default="/rkllm_chat")
    if not path.startswith("/"):
        path = f"/{path}"
    return host, path


def _local_reachable(host: str, timeout: float = 2.0) -> bool:
    host, _ = _clean_host_path(host, "/")
    if host.startswith("http://"):
        host = host[7:]
    elif host.startswith("https://"):
        host = host[8:]
    host = host.split("/")[0]
    if ":" in host:
        h, p = host.rsplit(":", 1)
        port = int(p)
    else:
        h, port = host, 8001
    try:
        with socket.create_connection((h, port), timeout=timeout):
            return True
    except OSError:
        return False


def _local_http_ping(host: str, path: str, timeout: float = 2.0) -> bool:
    base, p = _clean_host_path(host, path)
    for url in (f"{base}/", f"{base}{p}"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except Exception:
            continue
    return False


def _local_llm_available(host: str, path: str) -> bool:
    """Match bash ai_car_llm_port_open: port open or any HTTP <500 on / or /rkllm_chat."""
    if _local_reachable(host) or _local_http_ping(host, path):
        return True
    host, _ = _clean_host_path(host, path)
    try:
        import subprocess

        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "--max-time",
                "2",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                f"{host}/",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        code = (proc.stdout or "").strip()
        return code not in ("", "000")
    except Exception:
        return False


def check_startup(
    *,
    host: str = "",
    path: str = "/rkllm_chat",
    force: bool = False,
) -> StartupResult:
    """Resolve cloud/local/rules backend and user-facing status for UI."""
    bootstrap_voice_nav_env()
    normalize_voice_nav_env()
    host, path = _clean_host_path(
        host or os.environ.get("AI_CAR_LLM_HOST", "http://127.0.0.1:8001"),
        path or os.environ.get("AI_CAR_LLM_PATH", "/rkllm_chat"),
    )

    if not llm_intent.llm_intent_enabled():
        return StartupResult(
            ok=True,
            backend="rules",
            reason="rules",
            message="\u89c4\u5219+\u77e5\u8bc6\u5e93\u6a21\u5f0f\uff08\u672a\u5f00\u542f\u5927\u6a21\u578b\uff09",
            local_host=host,
        )

    mode = norm_env(os.environ.get("VOICE_NAV_BACKEND", "auto"), default="auto").lower()
    force_local = mode in ("local", "rkllm", "offline")
    force_cloud = mode in ("cloud", "online", "api")

    cloud_url = cloud_intent.cloud_base_url()
    cloud_model = cloud_intent.cloud_model()

    if force_local:
        if _local_llm_available(host, path):
            return StartupResult(
                ok=True,
                backend="local",
                reason="ok",
                message="\u672c\u5730\u5927\u6a21\u578b\u5c31\u7eea",
                local_host=host,
                cloud_url=cloud_url,
                cloud_model=cloud_model,
            )
        return StartupResult(
            ok=False,
            backend="local",
            reason="local_down",
            message="\u672c\u5730\u5927\u6a21\u578b\u672a\u542f\u52a8\uff0c\u8bf7\u5148\u542f\u52a8\u7ec8\u7aef4 flask_server",
            detail=host,
            local_host=host,
        )

    if cloud_intent.cloud_configured() and not force_local:
        if not net_probe.is_online(force=force):
            if force_cloud:
                return StartupResult(
                    ok=False,
                    backend="cloud",
                    reason="no_network",
                    message="\u65e0\u6cd5\u8fde\u63a5\u4e92\u8054\u7f51\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u540e\u91cd\u8bd5",
                    detail=cloud_url,
                    cloud_url=cloud_url,
                    cloud_model=cloud_model,
                    local_host=host,
                )
            if _local_llm_available(host, path):
                return StartupResult(
                    ok=True,
                    backend="local",
                    reason="no_network",
                    message="\u65e0\u6cd5\u8fde\u63a5\u4e92\u8054\u7f51\uff0c\u5df2\u5207\u6362\u672c\u5730\u5927\u6a21\u578b",
                    detail=cloud_url,
                    cloud_url=cloud_url,
                    cloud_model=cloud_model,
                    local_host=host,
                )
            return StartupResult(
                ok=False,
                backend="local",
                reason="no_network",
                message="\u65e0\u6cd5\u8fde\u63a5\u4e92\u8054\u7f51\uff0c\u4e14\u672c\u5730\u5927\u6a21\u578b\u672a\u542f\u52a8",
                detail=cloud_url,
                cloud_url=cloud_url,
                cloud_model=cloud_model,
                local_host=host,
            )

        status, detail = cloud_intent.probe_auth()
        if status == "ok":
            return StartupResult(
                ok=True,
                backend="cloud",
                reason="ok",
                message=f"\u4e91\u7aef\u5927\u6a21\u578b\u5c31\u7eea\uff08{cloud_model}\uff09",
                detail=detail,
                cloud_url=cloud_url,
                cloud_model=cloud_model,
                local_host=host,
            )
        if status == "invalid_api_key":
            if force_cloud:
                return StartupResult(
                    ok=False,
                    backend="cloud",
                    reason="invalid_api_key",
                    message="API Key \u65e0\u6548\u6216\u4e0e\u63a5\u5165\u5730\u5740\u4e0d\u5339\u914d\uff0c\u8bf7\u68c0\u67e5 DASHSCOPE_API_KEY \u4e0e DASHSCOPE_BASE_URL",
                    detail=detail,
                    cloud_url=cloud_url,
                    cloud_model=cloud_model,
                    local_host=host,
                )
            if _local_llm_available(host, path):
                return StartupResult(
                    ok=True,
                    backend="local",
                    reason="invalid_api_key",
                    message="API Key \u65e0\u6548\uff0c\u5df2\u5207\u6362\u672c\u5730\u5927\u6a21\u578b",
                    detail=detail,
                    cloud_url=cloud_url,
                    cloud_model=cloud_model,
                    local_host=host,
                )
            return StartupResult(
                ok=False,
                backend="cloud",
                reason="invalid_api_key",
                message="API Key \u65e0\u6548\uff0c\u4e14\u672c\u5730\u5927\u6a21\u578b\u672a\u542f\u52a8",
                detail=detail,
                cloud_url=cloud_url,
                cloud_model=cloud_model,
                local_host=host,
            )
        if status == "no_network":
            if _local_llm_available(host, path):
                return StartupResult(
                    ok=True,
                    backend="local",
                    reason="no_network",
                    message="\u65e0\u6cd5\u8fde\u63a5\u4e91\u7aef\uff0c\u5df2\u5207\u6362\u672c\u5730\u5927\u6a21\u578b",
                    detail=detail,
                    cloud_url=cloud_url,
                    cloud_model=cloud_model,
                    local_host=host,
                )
            return StartupResult(
                ok=False,
                backend="cloud",
                reason="no_network",
                message="\u65e0\u6cd5\u8fde\u63a5\u4e91\u7aef\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc",
                detail=detail,
                cloud_url=cloud_url,
                cloud_model=cloud_model,
                local_host=host,
            )
        if status == "misconfigured":
            return StartupResult(
                ok=False,
                backend="cloud",
                reason="not_configured",
                message="\u4e91\u7aef\u672a\u914d\u7f6e\u5b8c\u6574\uff08\u7f3a\u5c11 API Key \u6216 Base URL\uff09",
                detail=detail,
                cloud_url=cloud_url,
                cloud_model=cloud_model,
                local_host=host,
            )

    if force_cloud:
        return StartupResult(
            ok=False,
            backend="cloud",
            reason="not_configured",
            message="\u4e91\u7aef\u672a\u914d\u7f6e\uff08\u8bf7\u8bbe\u7f6e DASHSCOPE_API_KEY \u4e0e DASHSCOPE_BASE_URL\uff09",
            local_host=host,
        )

    if _local_llm_available(host, path):
        return StartupResult(
            ok=True,
            backend="local",
            reason="ok",
            message="\u672c\u5730\u5927\u6a21\u578b\u5c31\u7eea",
            local_host=host,
            cloud_url=cloud_url,
            cloud_model=cloud_model,
        )

    strict = os.environ.get("VOICE_NAV_STARTUP_STRICT", "0").strip().lower() in ("1", "true", "yes")
    if strict:
        return StartupResult(
            ok=False,
            backend="local",
            reason="local_down",
            message="\u672c\u5730\u5927\u6a21\u578b\u672a\u542f\u52a8\uff0c\u4e14\u4e91\u7aef\u672a\u914d\u7f6e\u6216\u4e0d\u53ef\u7528",
            local_host=host,
        )

    return StartupResult(
        ok=True,
        backend="rules",
        reason="local_down",
        message="\u5927\u6a21\u578b\u4e0d\u53ef\u7528\uff0c\u5df2\u964d\u7ea7\u4e3a\u89c4\u5219+\u77e5\u8bc6\u5e93",
        local_host=host,
    )


def apply_startup(result: StartupResult) -> None:
    """Pin backend for this process (used by backend_router)."""
    from . import backend_router

    backend_router.set_resolved_backend(result.backend)


def announce_startup(
    result: StartupResult,
    *,
    speak: bool = True,
    print_json: bool = False,
) -> None:
    tag = {
        "ok": "[\u542f\u52a8]",
        "no_network": "[\u7f51\u7edc]",
        "invalid_api_key": "[\u9274\u6743]",
        "not_configured": "[\u914d\u7f6e]",
        "local_down": "[\u672c\u5730]",
        "rules": "[\u542f\u52a8]",
        "error": "[\u9519\u8bef]",
    }.get(result.reason, "[\u542f\u52a8]")
    print(f"{tag} {result.message}", flush=True)
    if result.detail:
        print(f"  detail: {result.detail[:200]}", flush=True)
    print(
        f"  backend={result.backend} reason={result.reason} "
        f"cloud={result.cloud_model or '-'} local={result.local_host}",
        flush=True,
    )
    if print_json:
        print(f"VOICE_NAV_STARTUP_JSON={result.to_json()}", flush=True)
    if speak and os.environ.get("VOICE_NAV_TTS", "1").strip().lower() not in ("0", "false", "no"):
        if os.environ.get("VOICE_NAV_TTS_STARTUP", "1").strip().lower() not in ("0", "false", "no"):
            from . import tts

            tts.speak(result.message)
