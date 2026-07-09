#!/usr/bin/env bash
# 在 RockPi 上运行：同步 llm_intent.py + 修复 voice_nav_text.py
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

cp -a voice_nav/llm_intent.py "voice_nav/llm_intent.py.bak.$(date +%s)" 2>/dev/null || true

cat > voice_nav/llm_intent.py << 'EOF'
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
    return float(os.environ.get("VOICE_NAV_LLM_TIMEOUT", "8"))


def _llm_max_tokens() -> int:
    return int(os.environ.get("VOICE_NAV_LLM_MAX_TOKENS", "128"))


def _llm_retries() -> int:
    return max(0, int(os.environ.get("VOICE_NAV_LLM_RETRY", "0")))


def _snippet_max_chars() -> int:
    return int(os.environ.get("VOICE_NAV_LLM_SNIPPET_CHARS", "200"))


def compact_kb_snippets(hits: list[tuple[dict[str, Any], float]], *, max_chars: int | None = None) -> str:
    if not hits:
        return "无"
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
    if isinstance(body, dict):
        choices = body.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"])
        for key in ("response", "content", "text", "reply"):
            if body.get(key):
                return str(body[key])
    return str(body)


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
        return obj
    return None


def _fallback_intent_from_text(raw: str) -> dict[str, Any] | None:
    """When the model replies in plain text instead of JSON."""
    text = (raw or "").strip()
    if not text or "{" in text:
        return None
    if len(text) < 2:
        return None
    return {"intent": "clarify", "room_id": None, "reply": text}


def _parse_llm_object(raw: str) -> dict[str, Any] | None:
    obj = _extract_json(raw)
    if obj:
        return obj
    return _fallback_intent_from_text(raw)


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
    hits = hits or []
    if not should_use_llm_intent(intent, hits, user_text):
        return None

    kb_block = kb_snippets if kb_snippets.strip() else compact_kb_snippets(hits)
    prompt = (
        "你是楼内导览助手。根据【知识库】回答用户的问题，输出1行json。\n"
        "字段: intent (qa|navigate|qa_then_navigate|clarify|motion|cancel), "
        "room_id (字符串或null), reply (中文口语回复，简1-2句)\n"
        '示例: {"intent":"clarify","room_id":null,"reply":"知识库中没有食堂，请咨询前台。"}\n\n'
        f"【当前楼层】{current_floor}\n"
        f"【知识库】\n{kb_block}\n"
        f"【用户】{user_text}\n"
    )
    timeout = _llm_timeout_sec()
    retries = _llm_retries()
    t0 = time.monotonic()
    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            raw = _llm_chat(host, path, prompt, timeout)
            obj = _parse_llm_object(raw)
            elapsed = time.monotonic() - t0
            if obj and "intent" in obj:
                brief = str(obj.get("intent"))
                if obj.get("room_id"):
                    brief += f" room={obj.get('room_id')}"
                if obj.get("reply"):
                    reply_preview = str(obj["reply"]).replace("\n", " ")[:40]
                    brief += f" reply={reply_preview}…"
                print(f"[LLM] 意图 {elapsed:.1f}s -> {brief}", flush=True)
                return obj
            preview = (raw or "").replace("\n", " ")[:120]
            print(
                f"[LLM] 解析失败 {elapsed:.1f}s，原始回复: {preview}",
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
                print(f"[LLM] 超时/失败，重试 {attempt + 1}/{retries} ({exc})", flush=True)
                continue
            elapsed = time.monotonic() - t0
            print(f"[LLM] 超时/失败 {elapsed:.1f}s ({exc})，回退规则", flush=True)
            return None
    if last_exc is not None:
        elapsed = time.monotonic() - t0
        print(f"[LLM] 超时/失败 {elapsed:.1f}s ({last_exc})，回退规则", flush=True)
    return None
EOF

# 修复 voice_nav_text.py docstring 语法
if grep -q '"""from __future__' scripts/voice_nav_text.py 2>/dev/null; then
  sed -i 's/"""from __future__/"""\nfrom __future__/' scripts/voice_nav_text.py
fi

python3 -m py_compile voice_nav/llm_intent.py scripts/voice_nav_text.py
python3 -c "import sys; sys.path.insert(0,'.'); from voice_nav.llm_intent import _parse_llm_object; o=_parse_llm_object('知识库没有食堂'); print('parse OK', o)"

echo ""
echo "=== 同步完成 ==="
echo "测试命令:"
echo "  export VOICE_NAV_USE_LLM=1"
echo "  export VOICE_NAV_TTS=0"
echo "  python3 scripts/voice_nav_text.py \"楼内有食堂吗\""
