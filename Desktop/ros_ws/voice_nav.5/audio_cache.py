# -*- coding: utf-8 -*-
"""Pre-generated WAV cache for fixed / status TTS phrases."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

_PKG = Path(__file__).resolve().parent
_DEFAULT_CACHE = _PKG / "data" / "audio_cache"

_manifest: dict[str, Any] | None = None
_manifest_root: Path | None = None


def cache_dir() -> Path:
    raw = os.environ.get("VOICE_NAV_AUDIO_CACHE_DIR", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return _DEFAULT_CACHE


def _load_manifest() -> tuple[dict[str, Any], Path]:
    global _manifest, _manifest_root
    root = cache_dir()
    if _manifest is not None and _manifest_root == root:
        return _manifest, root
    path = root / "manifest.json"
    if not path.is_file():
        _manifest = {"version": 0, "entries": {}}
        _manifest_root = root
        return _manifest, root
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {"version": 0, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    _manifest = data
    _manifest_root = root
    return _manifest, root


def reload_manifest() -> None:
    global _manifest, _manifest_root
    _manifest = None
    _manifest_root = None
    _load_manifest()


def has_key(cache_key: str) -> bool:
    return resolve_path(cache_key) is not None


def resolve_path(cache_key: str) -> Optional[str]:
    key = (cache_key or "").strip()
    if not key:
        return None
    manifest, root = _load_manifest()
    entry = manifest.get("entries", {}).get(key)
    if not isinstance(entry, dict):
        return None
    rel = str(entry.get("path", "")).strip()
    if not rel:
        return None
    full = (root / rel).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        return None
    if full.is_file():
        return str(full)
    return None


def entry_text(cache_key: str) -> Optional[str]:
    manifest, _ = _load_manifest()
    entry = manifest.get("entries", {}).get((cache_key or "").strip())
    if isinstance(entry, dict):
        text = str(entry.get("text", "")).strip()
        return text or None
    return None


def room_nav_start_key(room_id: str) -> str:
    return f"room:{str(room_id).strip()}:nav_start"


def room_arrived_key(room_id: str) -> str:
    return f"room:{str(room_id).strip()}:arrived"


def status_key(nav_state: str) -> Optional[str]:
    mapping = {
        "GOING_TO_ELEVATOR": "status:going_elevator",
        "WAITING_ELEVATOR": "status:waiting_elevator",
        "SWITCHING_MAP": "status:switching_map",
        "NAVIGATING_TO_ROOM": "status:navigating_to_room",
        "IDLE": "status:nav_idle",
    }
    return mapping.get((nav_state or "").strip())
