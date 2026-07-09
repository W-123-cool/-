#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build pre-generated WAV cache for voice_nav status / fixed phrases."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROS_WS = Path(__file__).resolve().parent.parent
if str(ROS_WS) not in sys.path:
    sys.path.insert(0, str(ROS_WS))

from voice_nav import cache_tts, loader, wake  # noqa: E402

_PKG = ROS_WS / "voice_nav"
DEFAULT_OUT = _PKG / "data" / "audio_cache"


def _fixed_phrases() -> dict[str, tuple[str, str]]:
    """cache_key -> (relative path, text)"""
    return {
        "fixed:wake_reply": ("fixed/wake_reply.wav", wake.wake_reply()),
        "fixed:wake_stop": ("fixed/wake_stop.wav", wake.wake_stop_reply()),
        "fixed:nav_cancel": ("fixed/nav_cancel.wav", "\u5df2\u53d6\u6d88\u5bfc\u822a"),
        "fixed:session_bye": ("fixed/session_bye.wav", "\u597d\u7684\uff0c\u6709\u9700\u8981\u518d\u53eb\u6211"),
        "fixed:confirm_no": ("fixed/confirm_no.wav", "\u597d\u7684\uff0c\u5df2\u53d6\u6d88"),
        "fixed:vehicle_stop": ("fixed/vehicle_stop.wav", "\u5df2\u505c\u8f66"),
        "fixed:llm_unavailable": (
            "fixed/llm_unavailable.wav",
            "\u62b1\u6b49\uff0c\u5927\u6a21\u578b\u6682\u65f6\u65e0\u6cd5\u56de\u7b54\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002",
        ),
        "status:going_elevator": ("fixed/status_going_elevator.wav", "\u6b63\u5728\u524d\u5f80\u7535\u68af"),
        "status:waiting_elevator": ("fixed/status_waiting_elevator.wav", "\u6b63\u5728\u7b49\u5f85\u7535\u68af"),
        "status:switching_map": ("fixed/status_switching_map.wav", "\u6b63\u5728\u5207\u6362\u697c\u5c42\u5730\u56fe"),
        "status:navigating_to_room": (
            "fixed/status_navigating_to_room.wav",
            "\u6b63\u5728\u524d\u5f80\u76ee\u6807\u623f\u95f4",
        ),
        "status:nav_idle": ("fixed/status_nav_idle.wav", "\u5bfc\u822a\u7a7a\u95f2"),
    }


def _room_phrases(kb: dict) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for room in kb.get("rooms") or []:
        rid = str(room.get("id", "")).strip()
        name = str(room.get("name", rid)).strip()
        if not rid:
            continue
        out[f"room:{rid}:nav_start"] = (
            f"rooms/{rid}_nav_start.wav",
            f"\u6b63\u5728\u5e26\u60a8\u53bb{name}",
        )
        out[f"room:{rid}:arrived"] = (
            f"rooms/{rid}_arrived.wav",
            f"\u5df2\u5230\u8fbe{name}",
        )
    return out


def build_cache(
    out_dir: Path,
    *,
    force: bool = False,
    backend: str | None = None,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    kb = loader.load_knowledge()
    phrases: dict[str, tuple[str, str]] = {}
    phrases.update(_fixed_phrases())
    phrases.update(_room_phrases(kb))

    synth_backend = (backend or cache_tts.cache_tts_backend()).strip().lower()
    if synth_backend not in ("matcha", "espeak"):
        synth_backend = "matcha"
    print(f"[cache] synth backend={synth_backend}", flush=True)

    entries: dict[str, dict[str, str]] = {}
    built = 0
    for key, (rel, text) in sorted(phrases.items()):
        dst = out_dir / rel
        if force or not dst.is_file():
            if cache_tts.synth_cache_wav(text, dst, backend=synth_backend):
                built += 1
                print(f"[cache] {key} -> {rel}", flush=True)
            else:
                print(f"[cache] FAIL {key}", flush=True)
                continue
        entries[key] = {"path": rel.replace("\\", "/"), "text": text}

    manifest_backend = "matcha" if synth_backend == "matcha" else "espeak-ng"
    manifest = {"version": 2, "backend": manifest_backend, "entries": entries}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[cache] manifest: {len(entries)} entries, built {built} new wav", flush=True)
    return 0 if entries else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build voice_nav audio cache")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output directory (default: voice_nav/data/audio_cache)",
    )
    parser.add_argument("--force", action="store_true", help="Regenerate all wav files")
    parser.add_argument(
        "--backend",
        choices=("matcha", "espeak"),
        default=None,
        help="Synth backend (default: VOICE_NAV_CACHE_TTS_BACKEND or matcha)",
    )
    args = parser.parse_args()
    return build_cache(Path(args.out), force=args.force, backend=args.backend)


if __name__ == "__main__":
    raise SystemExit(main())
