#!/usr/bin/env python3
"""Check multi-fragment chain merge."""
from __future__ import annotations

import os
import sys

ROS_WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROS_WS)

from voice_nav.stt_filter import UtteranceMerger, is_complete_faq_question, is_incomplete_fragment


def main() -> None:
    m = UtteranceMerger(7.0)
    parts = ["\u653e\u884c\u674e", "\u7684\u5730\u65b9\u5728", "\u51e0\u697c"]
    last = None
    for p in parts:
        r, msg = m.push(p)
        print(p, "->", r, msg)
        last = r or last
    print("final", last, "complete", is_complete_faq_question(last or ""))

    m2 = UtteranceMerger(7.0)
    m2.push("\u884c\u674e\u5e94\u8be5")
    r2, msg2 = m2.push("\u653e\u5728\u54ea")
    print("luggage qa", r2, msg2)

    for t in ["\u653e\u884c\u674e", "\u7684\u5730\u65b9\u5728", "\u51e0\u697c", "\u653e\u884c\u674e\u7684\u5730\u65b9\u5728\u51e0\u697c"]:
        print(t, "incomplete", is_incomplete_fragment(t), "complete", is_complete_faq_question(t))


if __name__ == "__main__":
    main()
