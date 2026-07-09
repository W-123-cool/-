"""STT post-processing: spoken room numbers and utterance filter."""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from . import loader, retriever

_FRAGMENT_SUFFIXES = (
    "\u662f\u4ec0\u4e48", "\u662f\u556e", "\u5728\u54ea", "\u5728\u54ea\u91cc",
    "\u5e72\u4ec0\u4e48", "\u4ecb\u7ecd\u4e00\u4e0b", "\u4ecb\u7ecd",
    "\u6709\u4ec0\u4e48", "\u6709\u54ea\u4e9b",
)

_FRAGMENT_STANDALONE = (
    "\u623f\u95f4", "\u697c\u5c42", "\u4e00\u697c", "\u4e8c\u697c",
    "1\u697c", "2\u697c", "\u51e0\u697c", "\u54ea\u697c",
)

_LOCATION_TAIL = (
    "\u90a3\u8fb9", "\u8fd9\u8fb9", "\u90a3\u91cc", "\u8fd9\u91cc",
    "\u4e0a\u8fb9", "\u4e0b\u8fb9", "\u4e0a\u9762", "\u4e0b\u9762",
)

_QUESTION_SUFFIX_ONLY = (
    "\u662f\u5e72\u4ec0\u4e48\u7684", "\u662f\u505a\u4ec0\u4e48\u7684",
    "\u5e72\u4ec0\u4e48\u7684", "\u505a\u4ec0\u4e48\u7684", "\u6709\u4ec0\u4e48\u7528",
    "\u662f\u556e\u5e72\u7684", "\u662f\u5e72\u556e\u7684",
    "\u662f\u4ec0\u4e48", "\u662f\u556e", "\u5728\u54ea", "\u5728\u54ea\u91cc",
    "\u5e72\u4ec0\u4e48", "\u6709\u4ec0\u4e48", "\u6709\u54ea\u4e9b",
)

_CONFIRM_WORDS = (
    "\u597d", "\u597d\u7684", "\u662f", "\u662f\u7684", "\u53ef\u4ee5", "\u884c",
    "\u4e0d", "\u4e0d\u7528", "\u4e0d\u8981", "\u53d6\u6d88", "\u505c", "\u505c\u6b62",
)

_NAV_HINTS = (
    "\u53bb", "\u5230", "\u5bfc\u822a", "\u5e26\u6211", "\u524d\u5f80",
    "\u9001\u6211", "\u9886\u6211", "\u623f\u95f4", "\u697c",
    "\u524d\u53f0", "\u884c\u653f", "\u8d22\u52a1", "\u62a5\u9500", "\u4f1a\u8bae",
    "101", "102", "103", "104", "201", "202", "203", "204",
)

_FAQ_HINTS = (
    "\u884c\u674e", "\u5bc4\u5b58", "\u5b58\u653e", "\u653e\u5728\u54ea", "\u8be5\u53bb",
    "\u5e94\u8be5", "\u521a\u8fdb", "\u6765\u8bbf", "\u600e\u4e48\u8d70", "\u8be5\u53bb\u54ea",
    "\u53bb\u54ea\u91cc", "\u600e\u4e48\u529e",
    "\u98df\u5802", "\u997f", "\u5403\u996d",
)

_FOOD_MEAL_HINTS = (
    "\u6211\u997f\u4e86", "\u997f\u4e86", "\u597d\u997f", "\u60f3\u5403\u996d", "\u8981\u5403\u996d",
    "\u5403\u4ec0\u4e48", "\u53bb\u54ea\u5403", "\u996d\u5403", "\u6253\u996d",
    "\u98df\u5802", "\u9910\u5385", "\u996d\u5802", "\u5403\u996d",
)


def is_meal_intent(text: str) -> bool:
    """Hunger / meal requests — complete utterance, route to cafeteria KB."""
    t = (text or "").strip()
    if not t:
        return False
    if t in _FOOD_MEAL_HINTS:
        return True
    return any(k in t for k in _FOOD_MEAL_HINTS) and len(t) >= 2


_NAV_ONLY_STANDALONE = (
    "\u53bb", "\u5230", "\u5e26\u6211", "\u524d\u5f80", "\u9886\u6211", "\u5e26\u6211\u53bb",
)


def is_complete_faq_question(text: str) -> bool:
    """Full visitor FAQ utterance — do not treat as ASR fragment."""
    t = (text or "").strip()
    if not t:
        return False
    if is_meal_intent(t):
        return True
    if any(p in t for p in (
        "\u884c\u674e\u653e\u5728\u54ea", "\u884c\u674e\u653e\u54ea",
        "\u653e\u884c\u674e\u7684\u5730\u65b9\u5728\u51e0\u697c", "\u884c\u674e\u7684\u5730\u65b9\u5728\u51e0\u697c",
        "\u8be5\u53bb\u54ea", "\u8be5\u53bb\u54ea\u91cc", "\u521a\u8fdb\u697c\u8be5\u8be5",
        "\u521a\u8fdb\u697c\u8be5\u53bb", "\u600e\u4e48\u8d70",
    )):
        return True
    if "\u884c\u674e" in t and ("\u5728\u54ea" in t or "\u51e0\u697c" in t):
        return True
    if ("\u653e\u884c\u674e" in t or "\u884c\u674e\u653e" in t) and "\u5730\u65b9" in t and (
        "\u51e0\u697c" in t or "\u5728\u54ea" in t
    ):
        return True
    if ("\u8be5\u53bb" in t or "\u53bb\u54ea" in t or "\u53bb\u54ea\u91cc" in t) and len(t) >= 4:
        return True
    if "\u5728\u54ea" in t and len(t) >= 5:
        if any(k in t for k in (
            "\u884c\u674e", "\u524d\u53f0", "\u8d22\u52a1", "\u884c\u653f", "\u4eba\u4e8b",
            "\u4f1a\u8bae", "\u5bc4\u5b58", "\u5e94\u8be5",
        )):
            return True
    return False


def is_fragment_prefix_only(text: str) -> bool:
    """Subject/prefix cut off before the question suffix (e.g. 行李应该 / 刚进楼应)."""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("\u5e94\u8be5") and len(t) <= 10:
        return True
    if t.endswith("\u5e94") and len(t) <= 6:
        return True
    if "\u521a\u8fdb\u697c" in t and len(t) <= 8 and not retriever.looks_like_question(t):
        return True
    if t.endswith("\u884c\u674e") or t == "\u884c\u674e":
        return True
    if t.startswith("\u653e") and "\u884c\u674e" in t and len(t) <= 8:
        return True
    if t.startswith("\u7684") and len(t) <= 8:
        return True
    return False


def is_fragment_middle(text: str) -> bool:
    """Mid-sentence connective (e.g. 的地方在 / 的在哪)."""
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("\u7684") and len(t) <= 10:
        return True
    if t.endswith("\u5730\u65b9") and len(t) <= 8:
        return True
    if t in ("\u5730\u65b9\u5728", "\u7684\u5730\u65b9", "\u5728\u51e0\u697c", "\u51e0\u697c"):
        return True
    return False


def _smart_merge(carry: str, final: str) -> str:
    """Prefer the longer coherent utterance when ASR repeats/overlaps."""
    if not carry:
        return final
    if not final:
        return carry
    if carry in final:
        return final
    if final in carry:
        return carry
    return f"{carry}{final}"


def normalize_spoken_text(text: str) -> str:
    return retriever.normalize_room_text(text)


def extract_room_id(text: str) -> Optional[str]:
    return retriever.extract_room_id_from_text(text)


def is_question_suffix_only(text: str) -> bool:
    t = (text or "").strip()
    if is_complete_faq_question(t):
        return False
    if t in _QUESTION_SUFFIX_ONLY:
        return True
    nav_entity_keys = (
        "\u8d22\u52a1", "\u524d\u53f0", "\u884c\u653f", "\u62a5\u9500",
        "\u4f1a\u8bae", "\u529e\u516c", "\u4eba\u4e8b", "\u5ba2\u670d", "\u884c\u674e",
        "\u98df\u5802", "\u9910\u5385", "\u996d\u5802",
    )
    if any(k in t for k in nav_entity_keys):
        return False
    if is_meal_intent(t):
        return False
    if extract_room_id(normalize_spoken_text(t)):
        return False
    if retriever.looks_like_question(t) and len(t) > 8:
        return False
    for s in _QUESTION_SUFFIX_ONLY:
        if t == s:
            return True
        if t.endswith(s) and len(t) <= len(s) + 3:
            return True
    return False


def is_priority_utterance(text: str) -> bool:
    """Stop / confirm — allowed while TTS or pending navigation."""
    t = (text or "").strip()
    if not t:
        return False
    if t in _CONFIRM_WORDS:
        return True
    if any(k in t for k in ("\u505c\u6b62", "\u505c\u4e0b", "\u522b\u52a8", "\u505c\u8f66")) or t == "\u505c":
        return True
    if any(k in t for k in ("\u53d6\u6d88\u5bfc\u822a", "\u505c\u6b62\u5bfc\u822a", "\u4e0d\u8981\u5bfc\u822a")):
        return True
    return False


def is_incomplete_fragment(text: str) -> bool:
    """True when ASR likely cut mid-sentence; wait for more audio before acting."""
    t = (text or "").strip()
    if len(t) < 2:
        return True
    if is_priority_utterance(t) or t in _CONFIRM_WORDS:
        return False
    if is_complete_faq_question(t):
        return False
    if is_meal_intent(t):
        return False
    if t in _FRAGMENT_STANDALONE:
        return True
    if t in _FRAGMENT_SUFFIXES:
        return True
    if is_fragment_prefix_only(t):
        return True
    if is_fragment_middle(t):
        return True
    if "\u5730\u65b9" in t and "\u5728" in t and "\u51e0\u697c" not in t and "\u5728\u54ea" not in t:
        if len(t) <= 12:
            return True

    norm = normalize_spoken_text(t)
    if extract_room_id(norm) and retriever.looks_like_question(t):
        return False
    if extract_room_id(norm) and any(
        k in t for k in ("\u53bb", "\u5230", "\u5e26\u6211", "\u9886\u6211", "\u524d\u5f80")
    ):
        return False
    if extract_room_id(norm) and len(norm) <= 12:
        return True

    nav_entity_keys = (
        "\u8d22\u52a1", "\u524d\u53f0", "\u884c\u653f", "\u62a5\u9500",
        "\u4f1a\u8bae", "\u529e\u516c", "\u4eba\u4e8b", "\u5ba2\u670d", "\u884c\u674e",
        "\u98df\u5802", "\u9910\u5385", "\u996d\u5802",
    )
    if any(k in t for k in nav_entity_keys) and retriever.looks_like_question(t):
        return False

    if is_meal_intent(t):
        return False

    if is_question_suffix_only(t):
        return True

    if "\u623f\u95f4" in t and (
        "\u6709\u4ec0\u4e48" in t
        or any(f in t for f in ("\u4e00\u697c", "\u4e8c\u697c", "1\u697c", "2\u697c"))
    ):
        return False

    if any(f in t for f in ("\u4e00\u697c", "\u4e8c\u697c", "1\u697c", "2\u697c")):
        if "\u6709\u4ec0\u4e48" in t and "\u623f\u95f4" not in t:
            return True
        if len(t) <= 3:
            return True
        if not retriever.looks_like_question(t) and any(loc in t for loc in _LOCATION_TAIL):
            return True
        if not retriever.looks_like_question(t) and len(t) <= 8:
            return True

    if t in _NAV_ONLY_STANDALONE:
        return True
    if len(t) <= 3 and any(k in t for k in _NAV_ONLY_STANDALONE):
        return True
    if any(k in t for k in _NAV_ONLY_STANDALONE) and len(t) <= 12:
        if os.environ.get("VOICE_RELAX_FRAGMENT", "1").strip().lower() in ("1", "true", "yes"):
            return True

    if t in ("\u6709\u4ec0\u4e48", "\u6709\u54ea\u4e9b"):
        return True
    if len(t) <= 3 and t not in _CONFIRM_WORDS:
        return True
    if any(t.endswith(s) for s in _FRAGMENT_SUFFIXES) and len(t) <= 5:
        if is_complete_faq_question(t):
            return False
        return True
    return False


def is_new_question(text: str) -> bool:
    """New FAQ utterance while waiting for navigation confirm."""
    t = (text or "").strip()
    if not t or is_priority_utterance(t) or is_incomplete_fragment(t):
        return False
    if is_complete_faq_question(t) or retriever.looks_like_question(t):
        return True
    return len(t) >= 6 and any(k in t for k in _FAQ_HINTS + _NAV_HINTS)


def should_process(text: str, kb: dict[str, Any] | None = None) -> bool:
    raw = (text or "").strip()
    if len(raw) < 2:
        return False
    if is_incomplete_fragment(raw):
        return False
    norm = normalize_spoken_text(raw)
    if norm in _CONFIRM_WORDS or raw in _CONFIRM_WORDS:
        return True
    if extract_room_id(raw):
        return True
    if retriever.looks_like_question(raw) or retriever.looks_like_question(norm):
        return True
    if any(k in raw for k in _FRAGMENT_SUFFIXES):
        return True
    if any(k in raw or k in norm for k in _FAQ_HINTS):
        return True
    if any(k in raw or k in norm for k in _NAV_HINTS):
        return True
    if kb is None:
        kb = loader.load_knowledge()
    hits = retriever.search_rooms(kb, norm, top_k=1)
    if hits and hits[0][1] >= 4:
        return True
    if any(k in raw for k in ("\u884c\u674e", "\u5bc4\u5b58")) and hits and hits[0][1] >= 2:
        return True
    return False


def _nav_partial_keep(text: str) -> bool:
    """Keep merging nav utterances instead of discarding on idle timeout."""
    t = (text or "").strip()
    if not t:
        return False
    if any(k in t for k in _NAV_ONLY_STANDALONE):
        return True
    if any(k in t for k in _NAV_HINTS) and len(t) >= 2:
        return True
    return False


class UtteranceMerger:
    """Chain-merge ASR fragments until idle timeout or a complete utterance."""

    def __init__(self, idle_sec: float = 7.0) -> None:
        self._idle_sec = float(os.environ.get("VOICE_MERGE_SEC", str(idle_sec)))
        self._parts: list[str] = []
        self._last_ts = 0.0

    def _joined(self) -> str:
        merged = ""
        for part in self._parts:
            merged = _smart_merge(merged, part)
        return merged

    def has_pending(self) -> bool:
        return bool(self._parts)

    def pending_preview(self) -> str:
        return self._joined()

    def clear(self) -> bool:
        """Discard buffered merge fragments (e.g. after wake word re-collect)."""
        had = bool(self._parts)
        self._parts = []
        self._last_ts = 0.0
        return had

    def push(self, final: str) -> tuple[Optional[str], str]:
        final = (final or "").strip()
        if is_priority_utterance(final):
            self._parts = []
            return final, ""

        now = time.monotonic()

        if self._parts and final and now - self._last_ts > self._idle_sec:
            merged = self._joined()
            self._parts = []
            if merged and not is_incomplete_fragment(merged):
                self._parts = [final]
                self._last_ts = now
                merged2 = self._joined()
                if not is_incomplete_fragment(merged2):
                    self._parts = []
                    return merged2, f"[\u5408\u5e76] {merged2}"
                return None, f"[\u7b49\u5f85\u7eed\u8bf4] {merged2}\u2026"
            if merged:
                if (
                    os.environ.get("VOICE_RELAX_FRAGMENT", "1").strip().lower()
                    in ("1", "true", "yes")
                    and _nav_partial_keep(merged)
                ):
                    self._parts = [merged]
                    self._last_ts = now
                    return None, f"[\u7b49\u5f85\u7eed\u8bf4] {merged}\u2026"
                return None, f"[\u4e22\u5f03] \u8bed\u53e5\u4e0d\u5b8c\u6574: {merged}"

        if not final:
            return None, ""

        if not self._parts:
            self._parts = [final]
        else:
            self._parts.append(final)
        self._last_ts = now

        merged = self._joined()
        if not is_incomplete_fragment(merged):
            self._parts = []
            return merged, f"[\u5408\u5e76] {merged}"

        return None, f"[\u7b49\u5f85\u7eed\u8bf4] {merged}\u2026"
