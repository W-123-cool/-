#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test DashScope/MaaS cloud LLM connectivity (no mic)."""
from __future__ import annotations

import os
import sys

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROS_WS)

from voice_nav import cloud_intent, llm_intent


def main() -> None:
    if not cloud_intent.cloud_api_key():
        print("[err] export DASHSCOPE_API_KEY=sk-...", flush=True)
        sys.exit(1)
    if not cloud_intent.cloud_base_url() or "{WorkspaceId}" in cloud_intent.cloud_base_url():
        print("[err] export DASHSCOPE_WORKSPACE_ID=<your-workspace-id>", flush=True)
        print(
            "[hint] export DASHSCOPE_BASE_URL="
            "https://${DASHSCOPE_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            flush=True,
        )
        sys.exit(1)

    prompt = llm_intent.build_nav_prompt(
        "\u697c\u5185\u6709\u98df\u5802\u5417",
        "(\u65e0\u5339\u914d)",
        "?",
    )
    print(f"model={cloud_intent.cloud_model()}", flush=True)
    print(f"url={cloud_intent.cloud_base_url()}", flush=True)
    obj, elapsed, raw = cloud_intent.chat(prompt)
    print(f"elapsed={elapsed:.1f}s", flush=True)
    print(f"raw={raw[:300]}", flush=True)
    print(f"obj={obj}", flush=True)


if __name__ == "__main__":
    main()
