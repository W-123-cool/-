"""Multi-turn session state for pending navigation confirmation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Session:
    pending_room_id: Optional[str] = None
    pending_reply: str = ""
    last_intent: str = ""

    def set_pending_nav(self, room_id: str, reply: str = "") -> None:
        self.pending_room_id = str(room_id)
        self.pending_reply = reply
        self.last_intent = "pending_nav"

    def clear_pending(self) -> None:
        self.pending_room_id = None
        self.pending_reply = ""
        self.last_intent = ""

    def is_confirm_yes(self, text: str) -> bool:
        t = (text or "").strip()
        yes = (
            "\u597d", "\u597d\u7684", "\u662f", "\u662f\u7684", "\u5bf9", "\u53ef\u4ee5",
            "\u884c", "\u55ef", "\u53bb\u5427", "\u5e26\u6211\u53bb", "\u786e\u8ba4",
            "\u8981", "\u5e26\u6211\u8fc7\u53bb", "\u53bb\u5427",
        )
        return t in yes

    def is_confirm_no(self, text: str) -> bool:
        t = (text or "").strip()
        no = (
            "\u4e0d", "\u4e0d\u7528", "\u4e0d\u8981", "\u5426", "\u7b97\u4e86", "\u53d6\u6d88",
            "\u4e0d\u9700\u8981", "\u4e0d\u7528\u4e86", "\u4e0d\u53bb", "\u522b",
        )
        return t in no
