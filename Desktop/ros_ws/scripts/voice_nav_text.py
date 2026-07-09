#!/usr/bin/env python3
"""文字测试：导览/导航/问答（无需麦克风）。

用法:
  python3 scripts/voice_nav_text.py
  python3 scripts/voice_nav_text.py "财务在哪"
"""
from __future__ import annotations

import os
import sys

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROS_WS, "scripts")
sys.path.insert(0, ROS_WS)
sys.path.insert(0, SCRIPTS)

from voice_nav.agent import VoiceNavAgent
from voice_nav.startup_check import apply_startup, announce_startup, check_startup


def main() -> None:
    host = os.environ.get("AI_CAR_LLM_HOST", "http://127.0.0.1:8001")
    path = os.environ.get("AI_CAR_LLM_PATH", "/rkllm_chat")

    startup = check_startup(host=host, path=path, force=True)
    apply_startup(startup)
    announce_startup(startup, speak=False)

    agent = VoiceNavAgent()
    warmed = False

    one_shot = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else ""
    if one_shot:
        warmed, _ = agent.handle_text(one_shot, host=host, path=path, warmed=warmed)
        agent.close()
        return

    print("文字导览测试。输入 quit 退出。")
    print("示例: 财务在哪 | 介绍一下会议室 | 带我去201 | 一楼有什么房间")
    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if line.lower() in ("q", "quit", "exit"):
                break
            if not line:
                continue
            warmed, session_event = agent.handle_text(line, host=host, path=path, warmed=warmed)
            if session_event == "end_session":
                print("(会话结束)", flush=True)
    finally:
        agent.close()


if __name__ == "__main__":
    main()
