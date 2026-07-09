"""Keyword search over rooms.json (lightweight RAG, no embeddings)."""
from __future__ import annotations

import re
from typing import Any

_SPOKEN_DIGIT = str.maketrans(
    {
        "\u52a9": "\u4e00",
        "\u58f9": "\u4e00",
        "\u8d30": "\u4e8c",
        "\u53c1": "\u4e09",
        "\u8086": "\u56db",
        "\u4e24": "\u4e8c",
        "\u3007": "\u96f6",
    }
)

_CN_ROOM_SPOKEN = {
    "\u4e00\u96f6\u4e00": "101",
    "\u4e00\u96f6\u4e8c": "102",
    "\u4e00\u96f6\u4e09": "103",
    "\u4e00\u96f6\u56db": "104",
    "\u4e8c\u96f6\u4e00": "201",
    "\u4e8c\u96f6\u4e8c": "202",
    "\u4e8c\u96f6\u4e09": "203",
    "\u4e8c\u96f6\u56db": "204",
    "\u52a9\u96f6\u52a9": "101",
    "\u52a9\u96f6\u4e8c": "102",
    "\u52a9\u96f6\u4e09": "103",
    "\u52a9\u96f6\u56db": "104",
    "\u4e8c\u52a9\u96f6": "201",
    "\u4e8c\u52a9\u96f6\u4e8c": "202",
    "\u4e09\u96f6\u4e00": "301",
}

_ROOM_ID_RE = re.compile(r"(?<!\d)(10[1-4]|20[1-4]|301)(?!\d)")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def normalize_room_text(text: str) -> str:
    """ASR post-process: spoken digits -> room ids (e.g. 幺零幺 -> 101)."""
    t = _norm(text.translate(_SPOKEN_DIGIT))
    for spoken, rid in sorted(_CN_ROOM_SPOKEN.items(), key=lambda x: -len(x[0])):
        if spoken in t:
            t = t.replace(spoken, rid)
    return t


def _room_text(room: dict[str, Any]) -> str:
    parts = [
        str(room.get("id", "")),
        str(room.get("name", "")),
        str(room.get("floor", "")),
        " ".join(room.get("aliases") or []),
        " ".join(room.get("tags") or []),
        str(room.get("intro_short", "")),
        str(room.get("intro_detail", "")),
    ]
    return _norm(" ".join(parts))


def search_rooms(kb: dict[str, Any], query: str, top_k: int = 3) -> list[tuple[dict[str, Any], float]]:
    q = normalize_room_text(query)
    if not q:
        return []
    scored: list[tuple[dict[str, Any], float]] = []
    for room in kb.get("rooms") or []:
        if room.get("navigable") is False:
            continue
        score = 0.0
        rid = str(room.get("id", ""))
        name = _norm(str(room.get("name", "")))
        if rid and rid in q:
            score += 10.0
        if name and len(name) >= 2 and name in q:
            score += 8.0
        for alias in room.get("aliases") or []:
            a = _norm(str(alias))
            if len(a) >= 2 and a in q:
                score += 6.0
        for tag in room.get("tags") or []:
            t = _norm(str(tag))
            if len(t) >= 2 and t in q:
                score += 4.0
        blob = _room_text(room)
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|\d{3}", q):
            if token in blob:
                score += 2.0
        if score > 0:
            scored.append((room, score))
    scored.sort(key=lambda x: (-x[1], str(x[0].get("id", ""))))
    return scored[:top_k]


def extract_room_id_from_text(text: str) -> str | None:
    t = normalize_room_text(text or "")
    m = _ROOM_ID_RE.search(t)
    if m:
        return m.group(1)
    for spoken, rid in sorted(_CN_ROOM_SPOKEN.items(), key=lambda x: -len(x[0])):
        if spoken in t:
            return rid
    return None


def looks_like_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    qa_hints = (
        "\u5728\u54ea", "\u5728\u54ea\u91cc", "\u662f\u4ec0\u4e48", "\u662f\u556e", "\u5e72\u4ec0\u4e48",
        "\u4ecb\u7ecd", "\u4e86\u89e3", "\u54ea\u91cc", "\u600e\u4e48\u8d70",
        "\u5982\u4f55", "\u529e\u4ec0\u4e48", "\u627e\u8c01", "\u627e\u54ea",
        "\u8d1f\u8d23", "\u529f\u80fd", "\u7528\u9014", "\u5f00\u653e",
        "\u8425\u4e1a\u65f6\u95f4", "\u6709\u4ec0\u4e48", "\u6709\u54ea\u4e9b",
        "\u53bb\u54ea", "\u53bb\u54ea\u4e2a", "\u53bb\u54ea\u95f4", "\u53bb\u54ea\u91cc",
        "\u54ea\u4e2a\u623f\u95f4", "\u54ea\u95f4", "\u5f00\u4f1a", "\u8be5\u53bb",
        "\u653e\u5728", "\u653e\u54ea", "\u5bc4\u5b58",
    )
    if any(h in t for h in qa_hints):
        return True
    if "\u54ea\u4e2a" in t or "\u54ea\u95f4" in t:
        return True
    if t.endswith("\u5417") or t.endswith("\u5462") or t.endswith("\u554a"):
        return True
    return False


_EXPLICIT_NAV_PHRASES = (
    "\u5e26\u6211\u53bb", "\u9001\u6211\u53bb", "\u6211\u8981\u53bb", "\u524d\u5f80",
    "\u9886\u6211\u53bb", "\u5bfc\u822a", "\u9001\u6211\u5230", "\u5e26\u6211\u5230",
    "\u9886\u6211\u5230", "\u5e26\u6211\u8fc7\u53bb", "\u8fc7\u53bb",
)


def is_explicit_nav_request(text: str) -> bool:
    """True only when the user clearly asks to go somewhere (not a location question)."""
    t = (text or "").strip()
    if not t:
        return False
    if looks_like_question(t):
        return False
    if any(p in t for p in _EXPLICIT_NAV_PHRASES):
        return True
    rid = extract_room_id_from_text(t)
    if rid:
        compact = re.sub(r"\s+", "", t)
        if compact in (rid, f"\u53bb{rid}", f"\u5230{rid}"):
            return True
        if f"\u53bb{rid}" in compact or f"\u5230{rid}" in compact:
            return True
        if len(compact) <= 8:
            return True
    return False
