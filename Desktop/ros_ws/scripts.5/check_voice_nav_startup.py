#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Voice-nav startup probe for UI integration (prints JSON)."""
from __future__ import annotations

import argparse
import os
import sys

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROS_WS)

from voice_nav.startup_check import check_startup


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice nav LLM startup check")
    parser.add_argument("--host", default=os.environ.get("AI_CAR_LLM_HOST", "http://127.0.0.1:8001"))
    parser.add_argument("--path", default=os.environ.get("AI_CAR_LLM_PATH", "/rkllm_chat"))
    parser.add_argument("--json-only", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    result = check_startup(host=args.host, path=args.path, force=True)
    if args.json_only:
        print(result.to_json(), flush=True)
    else:
        from voice_nav.startup_check import announce_startup

        announce_startup(result, speak=False, print_json=True)
    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
