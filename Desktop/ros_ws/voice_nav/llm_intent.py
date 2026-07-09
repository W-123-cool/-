"""Optional RKLLM intent parsing (rules-first; LLM only when needed)."""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from . import retriever
from .stt_filter import is_incomplete_fragment


def llm_intent_enabled() -> bool:
    return os.environ.get("VOICE_NAV_USE_LLM", "0").strip().lower() in ("1", "true", "yes")


def _llm_mode() -> str:
    if not llm_intent_enabled():
        return "off"
    return os.environ.get("VOICE_NAV_LLM_MODE", "always").strip().lower()


def llm_only_answers() -> bool:
    """When True, Q&A uses LLM + knowledge base only (no rule-based answers)."""
    return llm_intent_enabled()


def _llm_timeout_sec() -> float:
    return float(os.environ.get("VOICE_NAV_LLM_TIMEOUT", "15"))


def _llm_max_tokens() -> int:
    return int(os.environ.get("VOICE_NAV_LLM_MAX_TOKENS", "128"))


def _llm_retries() -> int:
    return max(0, int(os.environ.get("VOICE_NAV_LLM_RETRY", "0")))


def _snippet_max_chars() -> int:
    return int(os.environ.get("VOICE_NAV_LLM_SNIPPET_CHARS", "200"))


def compact_kb_snippets(hits: list[tuple[dict[str, Any], float]], *, max_chars: int | None = None) -> str:
    if not hits:
        return "\u65e0"
    if max_chars is None:
        max_chars = _snippet_max_chars()
    lines: list[str] = []
    for room, score in hits[:3]:
        intro = str(room.get("intro_short") or room.get("intro") or "")[:max_chars]
        lines.append(
            f"{room.get('id')}/{room.get('name')}/{room.get('floor')}"
            f":{intro}(s={score:.0f})"
        )
    return "; ".join(lines)


def should_use_llm_intent(intent: str, hits: list[tuple[dict[str, Any], float]], text: str) -> bool:
    """When VOICE_NAV_USE_LLM=1, always call LLM (except incomplete fragments)."""
    if not llm_intent_enabled():
        return False

    if is_incomplete_fragment(text):
        return False

    mode = _llm_mode()
    if mode in ("0", "off", "never", "false", "no"):
        return False
    if mode in ("always", "all", "1", "true", "yes", "only", "llm_only"):
        return True

    # optional legacy fallback: rules first, LLM for ambiguous only
    if intent in ("cancel", "motion", "floor_list"):
        return False
    if retriever.extract_room_id_from_text(text) and intent in ("navigate", "qa", "qa_then_navigate"):
        return False
    if hits:
        top_score = hits[0][1]
        gap = top_score - (hits[1][1] if len(hits) > 1 else 0.0)
        if intent != "unknown" and top_score >= 6 and gap >= 3:
            return False
        if intent != "unknown" and top_score >= 5 and gap >= 5:
            return False
    return intent == "unknown" or not hits or hits[0][1] < 5


def _dig_llm_text(obj: Any) -> str:
    """Extract assistant text from various flask/RKLLM response shapes."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        for key in ("content", "response", "text", "reply", "message", "output", "result"):
            if obj.get(key):
                got = _dig_llm_text(obj[key])
                if got:
                    return got
        if obj.get("choices"):
            return _dig_llm_text(obj["choices"][0])
        if obj.get("data") is not None:
            return _dig_llm_text(obj["data"])
    if isinstance(obj, list) and obj:
        return _dig_llm_text(obj[0])
    return ""


def _llm_chat(host: str, path: str, prompt: str, timeout: float) -> str:
    url = host.rstrip("/") + path
    max_tokens = _llm_max_tokens()
    payload: dict[str, Any] = {
        "model": os.environ.get("AI_CAR_MODEL", "Octopus-v2"),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
    }
    temp = os.environ.get("VOICE_NAV_LLM_TEMPERATURE", "").strip()
    if temp:
        payload["temperature"] = float(temp)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    text = _dig_llm_text(body)
    if text:
        return text
    return str(body).strip()


_RTT_TAG_RE = re.compile(r"</?rtt_[^>]*>", re.IGNORECASE)


def _looks_like_rtt_garbage(text: str) -> bool:
    t = text or ""
    low = t.lower()
    return "<rtt" in low or "函数描述" in t or "函数逻辑" in t or "rtt_end" in low


def _extract_quoted_answer(text: str) -> Optional[str]:
    for pat in (
        r"则返回[「\"]([^」\"]+)[」\"]",
        r"返回[「\"]([^」\"]+)[」\"]",
        r"[「\"]([^」\"]{4,120})[」\"]",
    ):
        m = re.search(pat, text or "")
        if m:
            ans = m.group(1).strip()
            if ans and not _looks_like_rtt_garbage(ans):
                return ans
    return None


def _sanitize_llm_raw(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if _looks_like_rtt_garbage(text):
        quoted = _extract_quoted_answer(text)
        if quoted:
            return quoted
    text = _RTT_TAG_RE.sub("", text)
    text = re.sub(r"函数描述\s*:", "", text)
    text = re.sub(r"函数逻辑\s*:", "", text)
    lines: list[str] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("函数"):
            continue
        if _RTT_TAG_RE.search(ln):
            continue
        ln = re.sub(r"^\d+\.\s*", "", ln)
        if ln:
            lines.append(ln)
    cleaned = " ".join(lines).strip()
    if _looks_like_rtt_garbage(cleaned):
        return _extract_quoted_answer(cleaned) or ""
    return cleaned


def _normalize_llm_obj(obj: dict[str, Any]) -> dict[str, Any]:
    reply = str(obj.get("reply") or "").strip()
    if reply:
        clean = _sanitize_llm_raw(reply)
        if clean:
            obj["reply"] = clean
        else:
            quoted = _extract_quoted_answer(reply)
            if quoted:
                obj["reply"] = quoted
    return obj


def _extract_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    if "```" in text:
        block = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
        if block:
            text = block.group(1).strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    blob = m.group(0)
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        blob = blob.replace("'", '"')
        blob = re.sub(r",\s*}", "}", blob)
        blob = re.sub(r",\s*]", "]", blob)
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            return None
    if isinstance(obj, dict) and obj.get("intent"):
        return _normalize_llm_obj(obj)
    return None


def _loose_parse_intent(raw: str) -> dict[str, Any] | None:
    """Regex fallback for malformed JSON from small local models."""
    text = (raw or "").strip()
    if not text:
        return None
    intent_m = re.search(
        r'["\']?intent["\']?\s*[:=]\s*["\']?(qa|navigate|qa_then_navigate|clarify|motion|cancel)["\']?',
        text,
        flags=re.IGNORECASE,
    )
    reply_m = re.search(r'["\']?reply["\']?\s*[:=]\s*["\']([^"\']+)["\']', text)
    room_m = re.search(r'["\']?room_id["\']?\s*[:=]\s*["\']?(\d+|null)["\']?', text, flags=re.IGNORECASE)
    if not intent_m and not reply_m:
        return None
    intent = (intent_m.group(1) if intent_m else "clarify").lower()
    reply = reply_m.group(1).strip() if reply_m else re.sub(r"^[{\s\"']+|[}\s\"']+$", "", text)
    room_id: Any = None
    if room_m and room_m.group(1).lower() != "null":
        room_id = room_m.group(1)
    if not reply and len(text) >= 2:
        reply = text
    if not reply:
        return None
    result = {"intent": intent, "room_id": room_id, "reply": reply}
    return _normalize_llm_obj(result)


def _fallback_intent_from_text(raw: str) -> dict[str, Any] | None:
    """When the model replies in plain text instead of JSON."""
    cleaned = _sanitize_llm_raw(raw)
    for candidate in (cleaned, raw):
        if not candidate:
            continue
        loose = _loose_parse_intent(candidate)
        if loose:
            return _normalize_llm_obj(loose)
    text = (cleaned or raw or "").strip()
    if len(text) < 2:
        return None
    if _looks_like_rtt_garbage(text):
        quoted = _extract_quoted_answer(raw) or _extract_quoted_answer(text)
        if quoted:
            return {"intent": "clarify", "room_id": None, "reply": quoted}
        return None
    return {"intent": "clarify", "room_id": None, "reply": text}


def _parse_llm_object(raw: str) -> dict[str, Any] | None:
    cleaned = _sanitize_llm_raw(raw)
    for candidate in (raw, cleaned):
        if not candidate:
            continue
        obj = _extract_json(candidate)
        if obj:
            return obj
        loose = _loose_parse_intent(candidate)
        if loose:
            return _normalize_llm_obj(loose)
    return _fallback_intent_from_text(raw)


def parse_llm_response(raw: str) -> dict[str, Any] | None:
    """Public: parse model text into intent dict."""
    return _parse_llm_object(raw)


def build_nav_prompt(
    user_text: str,
    kb_snippets: str,
    current_floor: str,
    *,
    hits: list[tuple[dict[str, Any], float]] | None = None,
) -> str:
    hits = hits or []
    kb_block = kb_snippets if kb_snippets.strip() else compact_kb_snippets(hits)
    return (
        "\u4f60\u662f\u697c\u5185\u5bfc\u89c8\u52a9\u624b\uff08\u4e0d\u662f\u5c0f\u8f66\u63a7\u5236\u5668\uff09\u3002"
        "\u6839\u636e\u3010\u77e5\u8bc6\u5e93\u3011\u56de\u7b54\u7528\u6237\u7684\u95ee\u9898\uff0c\u8f93\u51fa1\u884cjson\u3002\n"
        "\u7981\u6b62\u8f93\u51fa rtt \u6807\u7b7e\u3001\u51fd\u6570\u63cf\u8ff0\u3001\u51fd\u6570\u903b\u8f91\uff0c\u53ea\u8f93\u51fa JSON\u3002\n"
        "\u89c4\u5219: \u8be2\u95ee\u4f4d\u7f6e/\u529e\u516c/\u5f00\u4f1a\u53bb\u54ea\u7b49\u95ee\u9898\u7528 intent=qa\uff0c\u5148\u56de\u7b54\u623f\u95f4\u4fe1\u606f\uff1b"
        "\u4ec5\u5f53\u7528\u6237\u660e\u786e\u8bf4\u5e26\u6211\u53bb/\u5bfc\u822a\u5230/\u53bb201 \u7b49\u65f6\u7528 intent=navigate\u3002\n"
        "\u5b57\u6bb5: intent (qa|navigate|qa_then_navigate|clarify|motion|cancel), "
        "room_id (\u5b57\u7b26\u4e32\u6216null), reply (\u4e2d\u6587\u53e3\u8bed\u56de\u590d\uff0c\u7b801-2\u53e5)\n"
        '\u793a\u4f8b: {"intent":"clarify","room_id":null,"reply":"\u77e5\u8bc6\u5e93\u4e2d\u6ca1\u6709\u98df\u5802\uff0c\u8bf7\u54a8\u8be2\u524d\u53f0\u3002"}\n\n'
        f"\u3010\u5f53\u524d\u697c\u5c42\u3011{current_floor}\n"
        f"\u3010\u77e5\u8bc6\u5e93\u3011\n{kb_block}\n"
        f"\u3010\u7528\u6237\u539f\u8bdd\u3011{user_text}\n"
    )


def log_intent_result(obj: dict[str, Any], elapsed: float, *, prefix: str = "[LLM]") -> None:
    brief = str(obj.get("intent"))
    if obj.get("room_id"):
        brief += f" room={obj.get('room_id')}"
    if obj.get("reply"):
        reply_preview = str(obj["reply"]).replace("\n", " ")[:40]
        brief += f" reply={reply_preview}…"
    print(f"{prefix} \u610f\u56fe {elapsed:.1f}s -> {brief}", flush=True)


def chat_local(prompt: str, host: str, path: str) -> dict[str, Any] | None:
    """Call local flask RKLLM and parse intent JSON."""
    timeout = _llm_timeout_sec()
    retries = _llm_retries()
    t0 = time.monotonic()
    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            raw = _llm_chat(host, path, prompt, timeout)
            if os.environ.get("VOICE_NAV_LLM_DEBUG", "0").strip().lower() in ("1", "true", "yes"):
                print(f"[LLM] raw={ (raw or '')[:200] }", flush=True)
            obj = _parse_llm_object(raw)
            elapsed = time.monotonic() - t0
            if obj and "intent" in obj:
                log_intent_result(obj, elapsed)
                return obj
            preview = (raw or "").replace("\n", " ")[:120]
            print(
                f"[LLM] \u89e3\u6790\u5931\u8d25 {elapsed:.1f}s\uff0c\u539f\u59cb\u56de\u590d: {preview}",
                flush=True,
            )
            return None
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            socket.timeout,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ) as exc:
            last_exc = exc
            if attempt < retries:
                print(f"[LLM] \u8d85\u65f6/\u5931\u8d25\uff0c\u91cd\u8bd5 {attempt + 1}/{retries} ({exc})", flush=True)
                continue
            elapsed = time.monotonic() - t0
            print(
                f"[LLM] \u8d85\u65f6/\u5931\u8d25 {elapsed:.1f}s ({exc})"
                f"\uff08\u53ef export VOICE_NAV_LLM_TIMEOUT=20\uff09",
                flush=True,
            )
            return None
    if last_exc is not None:
        elapsed = time.monotonic() - t0
        print(
            f"[LLM] \u8d85\u65f6/\u5931\u8d25 {elapsed:.1f}s ({last_exc})"
            f"\uff08\u53ef export VOICE_NAV_LLM_TIMEOUT=20\uff09",
            flush=True,
        )
    return None


def parse_intent_with_llm(
    user_text: str,
    kb_snippets: str,
    current_floor: str,
    host: str,
    path: str,
    *,
    intent: str = "unknown",
    hits: list[tuple[dict[str, Any], float]] | None = None,
) -> dict[str, Any] | None:
    """Local RKLLM only (legacy). Prefer backend_router.parse_intent_with_llm."""
    hits = hits or []
    if not should_use_llm_intent(intent, hits, user_text):
        return None
    prompt = build_nav_prompt(user_text, kb_snippets, current_floor, hits=hits)
    return chat_local(prompt, host, path)
