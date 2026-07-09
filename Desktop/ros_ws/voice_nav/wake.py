# -*- coding: utf-8 -*-
"""Wake-word gate via Sherpa KeywordSpotter (CPU) with text fallback."""
from __future__ import annotations

import os
import time
from typing import Any

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_KEYWORDS = os.path.join(_PKG_DIR, "data", "wake_keywords.txt")

_DEFAULT_WAKE_WORDS = "\u4f60\u597d\u5c0f\u8bfa,\u5c0f\u8bfa"
_DEFAULT_WAKE_REPLY = "\u6211\u5728"
_DEFAULT_WAKE_STOP_REPLY = (
    "\u5df2\u505c\u6b62\u5f53\u524d\u5bfc\u89c8\u4efb\u52a1\uff0c"
    "\u60a8\u73b0\u5728\u6709\u4ec0\u4e48\u9700\u6c42"
)


def _expand(path: str) -> str:
    return os.path.expanduser(path.strip())


def wake_enabled() -> bool:
    return os.environ.get("VOICE_WAKE_ENABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def wake_words() -> list[str]:
    raw = os.environ.get("VOICE_WAKE_WORDS", _DEFAULT_WAKE_WORDS).strip()
    return [w.strip() for w in raw.replace("\uff0c", ",").split(",") if w.strip()]


def wake_reply() -> str:
    return os.environ.get("VOICE_WAKE_REPLY", _DEFAULT_WAKE_REPLY).strip() or _DEFAULT_WAKE_REPLY


def wake_stop_reply() -> str:
    return (
        os.environ.get("VOICE_WAKE_STOP_REPLY", _DEFAULT_WAKE_STOP_REPLY).strip()
        or _DEFAULT_WAKE_STOP_REPLY
    )


def session_timeout_sec() -> float:
    try:
        return float(os.environ.get("VOICE_SESSION_TIMEOUT", "180"))
    except ValueError:
        return 180.0


def session_latched_enabled() -> bool:
    return os.environ.get("VOICE_SESSION_LATCH", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def fuzzy_wake_enabled() -> bool:
    return os.environ.get("VOICE_WAKE_FUZZY", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def adaptive_wake_threshold_enabled() -> bool:
    return os.environ.get("VOICE_WAKE_ADAPTIVE_THRESHOLD", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def text_has_wake_fuzzy(text: str) -> bool:
    """Homophone-tolerant wake match (e.g. 小闹 -> 小诺)."""
    if text_has_wake(text) or text_is_wake_only(text):
        return True
    if not fuzzy_wake_enabled():
        return False
    t = (text or "").strip().replace(" ", "")
    if not t:
        return False
    fuzzy_markers = (
        "\u5c0f\u8bfa",  # 小诺
        "\u5c0f\u95f9",  # 小闹
        "\u5c0f\u8882",  # 小袅
        "\u6653\u8bfa",  # 晓诺
        "\u4f60\u597d\u5c0f",  # 你好小
        "\u4f60\u597d\u6653",  # 你好晓
    )
    return any(m in t for m in fuzzy_markers)


def strip_wake_prefix(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    for w in sorted(wake_words(), key=len, reverse=True):
        if not w:
            continue
        if t == w:
            return ""
        if t.startswith(w):
            rest = t[len(w) :].strip(" \uff0c,.!?\u3002\uff01\uff1f")
            return rest
    return t


def text_is_wake_only(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return strip_wake_prefix(t) == "" and any(w in t for w in wake_words())


def text_has_wake(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return any(w in t for w in wake_words())


# Built-in ppinyin for default wake words (no sentencepiece required).
_BUILTIN_PPINYIN: dict[str, str] = {
    "\u5c0f\u8bfa": "x i\u01ceo n u\u00f2",
    "\u4f60\u597d\u5c0f\u8bfa": "n \u01d0 h \u01ceo x i\u01ceo n u\u00f2",
}


def write_keywords_file(
    model_dir: str,
    keywords_path: str,
    words: list[str] | None = None,
) -> bool:
    """Build UTF-8 keywords file on device (avoids Windows sync encoding issues)."""
    words = words or wake_words()
    if not words:
        return False
    tokens_path = os.path.join(model_dir, "tokens.txt")
    if not os.path.isfile(tokens_path):
        print(f"[wake] tokens.txt missing: {tokens_path}", flush=True)
        return False

    lines: list[str] = []
    encoded: list | None = None
    try:
        from sherpa_onnx import text2token

        encoded = text2token(words, tokens=tokens_path, tokens_type="ppinyin")
    except Exception as exc:
        print(
            f"[wake] text2token unavailable ({exc}); using built-in pinyin",
            flush=True,
        )

    if encoded is not None:
        for enc, word in zip(encoded, words):
            if isinstance(enc, list):
                part = " ".join(str(x) for x in enc)
            else:
                part = str(enc).strip()
            lines.append(f"{part} @{word}")
    else:
        for word in words:
            pinyin = _BUILTIN_PPINYIN.get(word)
            if not pinyin:
                print(
                    f"[wake] no built-in pinyin for {word!r}; "
                    "pip install sentencepiece or add to _BUILTIN_PPINYIN",
                    flush=True,
                )
                return False
            lines.append(f"{pinyin} @{word}")

    parent = os.path.dirname(keywords_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(keywords_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[wake] wrote keywords: {keywords_path}", flush=True)
    for ln in lines:
        print(f"  {ln}", flush=True)
    return True


class WakeGate:
    """Streaming KWS; falls back to text match when model is unavailable."""

    def __init__(self) -> None:
        self.enabled = wake_enabled()
        self._spotter: Any = None
        self._stream: Any = None
        self._use_kws = False
        self.kws_available = False
        self._listen_active = bool(self.enabled)
        self._listen_mode = "boot"
        self._current_threshold = 0.25
        self._current_score = 1.0
        self._last_noise_floor = _float_env("VOICE_NAV_DENOISE_QUIET_RMS", 0.012)
        self._last_thr_rebuild = 0.0
        if not self.enabled:
            return
        self._load_kws()

    def _kws_threshold(self, *, mission: bool) -> float:
        if mission:
            raw = os.environ.get(
                "VOICE_MISSION_WAKE_THRESHOLD",
                os.environ.get("VOICE_WAKE_THRESHOLD", "0.20"),
            )
        else:
            raw = os.environ.get("VOICE_WAKE_THRESHOLD", "0.20")
        try:
            base = float(raw)
        except ValueError:
            base = 0.20
        return self._apply_noise_adjustment(base)

    def _apply_noise_adjustment(self, base: float) -> float:
        if not adaptive_wake_threshold_enabled():
            return base
        floor = self._last_noise_floor
        quiet = _float_env("VOICE_NAV_DENOISE_QUIET_RMS", 0.012)
        adj_min = _float_env("VOICE_WAKE_THRESHOLD_ADJ_MIN", -0.03)
        adj_max = _float_env("VOICE_WAKE_THRESHOLD_ADJ_MAX", 0.06)
        adj = (floor - quiet) * _float_env("VOICE_WAKE_THRESHOLD_NOISE_GAIN", 0.8)
        adj = max(adj_min, min(adj_max, adj))
        return max(0.08, min(0.35, base + adj))

    def note_noise_floor(self, floor: float) -> None:
        """Update adaptive KWS threshold from streaming noise floor estimate."""
        if floor <= 0.0:
            return
        self._last_noise_floor = floor
        if not adaptive_wake_threshold_enabled() or not self._use_kws:
            return
        now = time.monotonic()
        min_interval = _float_env("VOICE_WAKE_THRESHOLD_REBUILD_SEC", 5.0)
        if now - self._last_thr_rebuild < min_interval:
            return
        mission = self._listen_mode == "mission"
        want_thr = self._kws_threshold(mission=mission)
        if abs(want_thr - self._current_threshold) < 0.015:
            return
        self._ensure_listen_mode(self._listen_mode)
        self._last_thr_rebuild = now
        print(
            f"[wake] adaptive threshold -> {want_thr:.3f} "
            f"(noise_floor={floor:.4f}, mode={self._listen_mode})",
            flush=True,
        )

    def _kws_score(self, *, mission: bool) -> float:
        key = "VOICE_MISSION_WAKE_SCORE" if mission else "VOICE_WAKE_SCORE"
        fallback = os.environ.get("VOICE_WAKE_SCORE", "1.0")
        try:
            return float(os.environ.get(key, fallback))
        except ValueError:
            return 1.0

    def _build_spotter(self, *, mission: bool) -> None:
        model_dir = _expand(
            os.environ.get(
                "VOICE_WAKE_MODEL_DIR",
                "~/Desktop/rk3588-offline-bundle/model/"
                "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile",
            )
        )
        keywords_file = _expand(
            os.environ.get("VOICE_WAKE_KEYWORDS_FILE", _DEFAULT_KEYWORDS)
        )
        enc = os.path.join(model_dir, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
        dec = os.path.join(model_dir, "decoder-epoch-12-avg-2-chunk-16-left-64.onnx")
        joi = os.path.join(model_dir, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
        tok = os.path.join(model_dir, "tokens.txt")
        threshold = self._kws_threshold(mission=mission)
        score = self._kws_score(mission=mission)
        import sherpa_onnx

        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=tok,
            encoder=enc,
            decoder=dec,
            joiner=joi,
            keywords_file=keywords_file,
            num_threads=int(os.environ.get("VOICE_WAKE_THREADS", "1")),
            provider=os.environ.get("VOICE_WAKE_PROVIDER", "cpu").strip() or "cpu",
            keywords_score=score,
            keywords_threshold=threshold,
        )
        self._stream = self._spotter.create_stream()
        self._current_threshold = threshold
        self._current_score = score
        self._listen_mode = "mission" if mission else "boot"

    def _load_kws(self) -> None:
        model_dir = _expand(
            os.environ.get(
                "VOICE_WAKE_MODEL_DIR",
                "~/Desktop/rk3588-offline-bundle/model/"
                "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile",
            )
        )
        keywords_file = _expand(
            os.environ.get("VOICE_WAKE_KEYWORDS_FILE", _DEFAULT_KEYWORDS)
        )
        if not os.path.isdir(model_dir):
            print(
                f"[wake] KWS model dir missing: {model_dir}; using text fallback",
                flush=True,
            )
            return
        enc = os.path.join(model_dir, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
        dec = os.path.join(model_dir, "decoder-epoch-12-avg-2-chunk-16-left-64.onnx")
        joi = os.path.join(model_dir, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
        tok = os.path.join(model_dir, "tokens.txt")
        if not all(os.path.isfile(p) for p in (enc, dec, joi, tok)):
            print("[wake] KWS onnx files incomplete; using text fallback", flush=True)
            return

        regen = os.environ.get("VOICE_WAKE_REGEN_KEYWORDS", "1").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if regen or not os.path.isfile(keywords_file):
            if not write_keywords_file(model_dir, keywords_file):
                print("[wake] cannot build keywords file; using text fallback", flush=True)
                return
        elif not os.path.isfile(keywords_file):
            print(f"[wake] keywords file missing: {keywords_file}", flush=True)
            return
        try:
            print("[wake] loading KWS (CPU)...", flush=True)
            self._build_spotter(mission=False)
            self._use_kws = True
            self.kws_available = True
            self._listen_active = True
            print(f"[wake] KWS ready: {model_dir}", flush=True)
        except Exception as exc:
            print(f"[wake] KWS init failed: {exc}; text fallback", flush=True)
        if not hasattr(self, "_listen_active"):
            self._listen_active = False

    def _ensure_listen_mode(self, mode: str) -> None:
        if not self._use_kws or not self.kws_available:
            return
        mission = mode == "mission"
        want_thr = self._kws_threshold(mission=mission)
        want_score = self._kws_score(mission=mission)
        if self._listen_mode != mode or want_thr != self._current_threshold or want_score != self._current_score:
            self._build_spotter(mission=mission)
            print(
                f"[wake] KWS mode={mode} threshold={want_thr} score={want_score}",
                flush=True,
            )

    @property
    def listening(self) -> bool:
        return bool(self.enabled and self._listen_active)

    def pause_after_wake(self) -> None:
        """首次唤醒后关闭 KWS，释放 CPU。"""
        self._listen_active = False
        self.reset()
        print("[wake] KWS 已暂停（首次唤醒完成）", flush=True)

    def resume_boot_listen(self) -> None:
        """待机唤醒：重新开启 KWS。"""
        if not self.enabled or not self.kws_available:
            return
        self._ensure_listen_mode("boot")
        self._listen_active = True
        self.reset()
        print("[wake] KWS 已开启（待机唤醒）", flush=True)

    def resume_mission_listen(self) -> None:
        """导览途中仅用于唤醒截停。"""
        if not self.enabled or not self.kws_available:
            return
        self._ensure_listen_mode("mission")
        self._listen_active = True
        self.reset()
        thr = self._current_threshold
        print(f"[wake] KWS 已开启（导览截停） threshold={thr}", flush=True)

    def reset(self) -> None:
        if self._use_kws and self._spotter is not None and self._stream is not None:
            self._spotter.reset_stream(self._stream)

    def feed(self, sample_rate: int, samples: list[float]) -> bool:
        if not self.enabled or not self._listen_active:
            return False
        if self._use_kws and self._spotter is not None and self._stream is not None:
            self._stream.accept_waveform(sample_rate, samples)
            while self._spotter.is_ready(self._stream):
                self._spotter.decode_stream(self._stream)
                result = self._spotter.get_result(self._stream)
                if result:
                    print(f"[wake] detected: {result}", flush=True)
                    self._spotter.reset_stream(self._stream)
                    return True
            return False
        return False

    def match_text(self, text: str) -> bool:
        if not self.enabled:
            return True
        if fuzzy_wake_enabled():
            return text_has_wake_fuzzy(text)
        return text_has_wake(text) or text_is_wake_only(text)
