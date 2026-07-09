#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate wake_keywords.txt with sherpa text2token (run on RockPi)."""
from __future__ import annotations

import os
import sys

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROS_WS not in sys.path:
    sys.path.insert(0, ROS_WS)

from voice_nav.wake import wake_words, write_keywords_file


def main() -> None:
    model_dir = os.path.expanduser(
        os.environ.get(
            "VOICE_WAKE_MODEL_DIR",
            "~/Desktop/rk3588-offline-bundle/model/"
            "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile",
        )
    )
    out = os.path.expanduser(
        os.environ.get(
            "VOICE_WAKE_KEYWORDS_FILE",
            os.path.join(ROS_WS, "voice_nav", "data", "wake_keywords.txt"),
        )
    )
    words = wake_words()
    print(f"model: {model_dir}", flush=True)
    print(f"words: {words}", flush=True)
    if not write_keywords_file(model_dir, out, words):
        sys.exit(1)


if __name__ == "__main__":
    main()
