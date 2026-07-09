#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick test for Sherpa Matcha TTS on RockPi."""
from __future__ import annotations

import os
import sys

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROS_WS not in sys.path:
    sys.path.insert(0, ROS_WS)

from voice_nav import tts

# Default test phrase (unicode escapes for cross-platform encoding)
_DEFAULT_TEXT = (
    "\u4f60\u597d\uff0c\u6b22\u8fce\u4e58\u5750"
    "\u697c\u5185\u5bfc\u89c8\u673a\u5668\u4eba\u3002"
)
TEXT = os.environ.get("TTS_TEST_TEXT", _DEFAULT_TEXT)


def main() -> None:
    os.environ.setdefault("VOICE_NAV_TTS_BACKEND", "sherpa")
    os.environ["VOICE_NAV_TTS_SYNC"] = "1"
    print(f"backend={os.environ.get('VOICE_NAV_TTS_BACKEND')}  text={TEXT!r}", flush=True)
    print(f"sink={os.environ.get('VOICE_NAV_PULSE_SINK', '(default)')}", flush=True)
    tts.speak(TEXT)
    print("done (audio played if successful)", flush=True)


if __name__ == "__main__":
    main()
