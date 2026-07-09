#!/usr/bin/env python3
"""Check LLM intent fast-path vs fallback."""
from __future__ import annotations

import os
import sys

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROS_WS)

os.environ["VOICE_NAV_USE_LLM"] = "1"
os.environ["VOICE_NAV_LLM_MODE"] = "always"

from voice_nav import llm_intent, retriever, loader

kb = loader.load_knowledge()


def hits_for(text: str):
    return retriever.search_rooms(kb, text, top_k=3)


def check(text: str, intent: str, want: bool) -> None:
    h = hits_for(text)
    got = llm_intent.should_use_llm_intent(intent, h, text)
    status = "OK" if got == want else "FAIL"
    print(f"{status} {text!r} intent={intent} llm={got} want={want}")


def main() -> None:
    check("\u8d22\u52a1\u5728\u54ea", "qa", True)
    check("\u53bb201", "navigate", True)
    check("\u4e00\u697c\u6709\u4ec0\u4e48\u623f\u95f4", "floor_list", True)
    check("\u90a3\u8fb9\u662f\u529e\u4ec0\u4e48\u5730\u65b9", "unknown", True)
    os.environ["VOICE_NAV_LLM_MODE"] = "fallback"
    check("\u8d22\u52a1\u5728\u54ea", "qa", False)


if __name__ == "__main__":
    main()
