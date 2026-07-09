# -*- coding: utf-8 -*-
"""Route voice-nav LLM: cloud API (online) or local RKLLM (offline)."""
from __future__ import annotations

import os
from typing import Any, Optional

from . import cloud_intent, llm_intent, loader, net_probe

_resolved_backend: Optional[str] = None


def set_resolved_backend(mode: str) -> None:
    global _resolved_backend
    m = (mode or "").strip().lower()
    if m in ("cloud", "local", "rules"):
        _resolved_backend = m


def clear_resolved_backend() -> None:
    global _resolved_backend
    _resolved_backend = None


def backend_mode_label() -> str:
    if not llm_intent.llm_intent_enabled():
        return "rules"
    backend = resolve_backend(dry_run=True)
    if backend == "cloud":
        return f"cloud({cloud_intent.cloud_model()})"
    if backend == "local":
        return "local"
    return "rules"


def resolve_backend(*, dry_run: bool = False) -> str:
    global _resolved_backend
    if _resolved_backend and not dry_run:
        return _resolved_backend

    if not llm_intent.llm_intent_enabled():
        return "rules"

    mode = os.environ.get("VOICE_NAV_BACKEND", "auto").strip().lower()
    if mode in ("local", "rkllm", "offline"):
        return "local"
    if mode in ("cloud", "online", "api"):
        return "cloud" if cloud_intent.cloud_configured() else "local"
    if cloud_intent.cloud_configured():
        if dry_run:
            return "cloud" if net_probe.is_online() else "local"
        if net_probe.is_online():
            return "cloud"
    return "local"


def _kb_mode_full(backend: str) -> bool:
    if backend == "cloud":
        return True
    mode = os.environ.get("VOICE_NAV_KB_MODE", "auto").strip().lower()
    return mode in ("full", "all", "1", "true", "yes")


def parse_intent_with_llm(
    user_text: str,
    kb_snippets: str,
    current_floor: str,
    host: str,
    path: str,
    *,
    intent: str = "unknown",
    hits: list[tuple[dict[str, Any], float]] | None = None,
    kb: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    hits = hits or []
    if not llm_intent.should_use_llm_intent(intent, hits, user_text):
        return None

    backend = resolve_backend()
    if _kb_mode_full(backend) and kb:
        kb_block = loader.format_kb_for_llm(kb)
    elif kb_snippets.strip():
        kb_block = kb_snippets
    else:
        kb_block = llm_intent.compact_kb_snippets(hits)

    prompt = llm_intent.build_nav_prompt(
        user_text, kb_block, current_floor, hits=hits,
    )
    force_cloud = os.environ.get("VOICE_NAV_BACKEND", "auto").strip().lower() in (
        "cloud", "online", "api",
    )

    if backend == "cloud":
        print(f"[\u540e\u7aef] \u4e91\u7aef API ({cloud_intent.cloud_model()}) \u5168\u91cf\u77e5\u8bc6\u5e93", flush=True)
        obj, elapsed, raw = cloud_intent.chat(prompt)
        if obj and obj.get("intent"):
            llm_intent.log_intent_result(obj, elapsed, prefix="[\u4e91\u7aef]")
            return obj
        preview = (raw or "").replace("\n", " ")[:120]
        print(
            f"[\u4e91\u7aef] \u89e3\u6790\u5931\u8d25 {elapsed:.1f}s\uff0c\u539f\u59cb\u56de\u590d: {preview}",
            flush=True,
        )
        if force_cloud:
            return None
        print("[\u540e\u7aef] \u964d\u7ea7\u672c\u5730 RKLLM", flush=True)

    print("[\u540e\u7aef] \u672c\u5730 RKLLM", flush=True)
    return llm_intent.chat_local(prompt, host, path)
