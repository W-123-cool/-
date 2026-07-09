"""Network reachability probe (cached)."""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

_cache_ts = 0.0
_cache_online = False


def _check_url() -> str:
    url = os.environ.get("VOICE_NAV_NET_CHECK_URL", "").strip()
    if url:
        return url
    base = os.environ.get("VOICE_NAV_CLOUD_BASE_URL", "").strip()
    if not base:
        base = os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).strip()
    return base.rstrip("/") + "/"


def is_online(*, force: bool = False) -> bool:
    global _cache_ts, _cache_online
    ttl = float(os.environ.get("VOICE_NAV_NET_CHECK_SEC", "30"))
    now = time.monotonic()
    if not force and now - _cache_ts < ttl:
        return _cache_online

    url = _check_url()
    timeout = float(os.environ.get("VOICE_NAV_NET_CHECK_TIMEOUT", "3"))
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _cache_online = resp.status < 500
    except urllib.error.HTTPError as exc:
        _cache_online = exc.code < 500
    except Exception:
        _cache_online = False

    _cache_ts = now
    return _cache_online
