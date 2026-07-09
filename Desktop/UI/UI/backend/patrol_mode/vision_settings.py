# -*- coding: utf-8 -*-
"""Security vision toggles and detection confidence (PC persistence)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from patrol_mode.config import ensure_data_dir

_VISION_SETTINGS_FILE = ensure_data_dir() / "vision_settings.json"


@dataclass
class VisionSettings:
    patrol_track_enabled: bool = True
    guard_view_track_enabled: bool = True
    detection_conf: float = 0.30

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisionSettings:
        conf = float(data.get("detection_conf", 0.30))
        conf = max(0.05, min(0.95, conf))
        return cls(
            patrol_track_enabled=bool(data.get("patrol_track_enabled", True)),
            guard_view_track_enabled=bool(data.get("guard_view_track_enabled", True)),
            detection_conf=conf,
        )


def load_vision_settings() -> VisionSettings:
    if not _VISION_SETTINGS_FILE.is_file():
        cfg = VisionSettings()
        save_vision_settings(cfg)
        return cfg
    try:
        data = json.loads(_VISION_SETTINGS_FILE.read_text(encoding="utf-8"))
        return VisionSettings.from_dict(data if isinstance(data, dict) else {})
    except Exception:
        return VisionSettings()


def save_vision_settings(cfg: VisionSettings) -> None:
    ensure_data_dir()
    _VISION_SETTINGS_FILE.write_text(
        json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
