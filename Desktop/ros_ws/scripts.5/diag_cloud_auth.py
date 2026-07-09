#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnose DashScope / MaaS cloud auth (tries common base URLs)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROS_WS)

from voice_nav import cloud_intent


def _probe(base: str, model: str, api_key: str) -> None:
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    print(f"\n=== try base={base} model={model} ===", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = ""
        choices = body.get("choices") or []
        if choices:
            content = str((choices[0].get("message") or {}).get("content") or "")
        print(f"OK status={resp.status} reply={content[:80]!r}", flush=True)
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")[:400]
        print(f"FAIL HTTP {exc.code} {exc.reason}", flush=True)
        print(f"body: {err}", flush=True)
    except Exception as exc:
        print(f"FAIL {exc}", flush=True)


def main() -> None:
    api_key = cloud_intent.cloud_api_key()
    if not api_key:
        print("[err] export DASHSCOPE_API_KEY first", flush=True)
        sys.exit(1)

    ws = os.environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
    model = cloud_intent.cloud_model()
    key_hint = api_key[:8] + "..." + api_key[-4:]
    print(f"key={key_hint} len={len(api_key)} workspace={ws or '-'}", flush=True)

    candidates = []
    cur = cloud_intent.cloud_base_url()
    if cur:
        candidates.append(cur)
    if ws:
        candidates.append(f"https://{ws}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    # Token Plan team keys (sk-ws-*) use this URL, not workspace MaaS URL.
    candidates.append("https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    candidates.append("https://dashscope.aliyuncs.com/compatible-mode/v1")

    seen: set[str] = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        _probe(base, model, api_key)

    alt = os.environ.get("DASHSCOPE_MODEL_ALT", "qwen3.7-max,qwen3.6-plus,qwen-plus").strip()
    for m in [x.strip() for x in alt.split(",") if x.strip()]:
        if m != model and cur:
            _probe(cur, m, api_key)
    token_base = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    if token_base not in seen:
        for m in [model, "qwen3.7-max", "qwen3.6-plus", "qwen-plus"]:
            _probe(token_base, m, api_key)


if __name__ == "__main__":
    main()
