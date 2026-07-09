"""PC-side alert capture when vehicle reports security_person_event via MQTT."""
from __future__ import annotations

import logging
import threading
import time
import urllib.request
from typing import Any, Optional

from patrol_mode.alerts import get_alert_store
from patrol_mode.config import PATROL_CAMERA_STREAM_URL, PERSON_EVENT_COOLDOWN_SEC

_LOG = logging.getLogger("patrol.alert_capture")
_lock = threading.Lock()
_last_capture_mono = 0.0


def _resolve_frame_url() -> str:
    stream = ""
    try:
        from mqtt_robot_bridge import bridge_enabled, get_bridge

        if bridge_enabled():
            mqtt = (get_bridge().snapshot().get("mqtt") or {})
            if isinstance(mqtt, dict):
                stream = str(mqtt.get("camera_stream_url", "") or "").strip()
    except Exception:
        pass
    if not stream:
        stream = (PATROL_CAMERA_STREAM_URL or "").strip()
    if not stream:
        return ""
    base = stream.split("?", 1)[0].rstrip("/")
    if base.endswith("/stream"):
        return base[: -len("/stream")] + "/frame.jpg"
    return base + "/frame.jpg"


def capture_alert_from_person_event(data: dict[str, Any]) -> None:
    """Fetch vehicle /frame.jpg and store alert (backup when vehicle HTTP upload fails)."""
    global _last_capture_mono
    now = time.monotonic()
    with _lock:
        if now - _last_capture_mono < PERSON_EVENT_COOLDOWN_SEC:
            return
        _last_capture_mono = now

    frame_url = _resolve_frame_url()
    if not frame_url:
        _LOG.warning(
            "person_event alert skip: no camera URL "
            "(wait for vehicle patrol_camera_stream or set PATROL_CAMERA_STREAM_URL on PC)"
        )
        return

    payload = dict(data)

    def _worker() -> None:
        try:
            req = urllib.request.Request(
                frame_url, headers={"User-Agent": "NovaJoySecurityAlert/1.0"}
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                jpeg = resp.read()
            if len(jpeg) < 100:
                _LOG.warning("person_event alert skip: frame too small (%d bytes)", len(jpeg))
                return
            meta: dict[str, Any] = {
                "sub_state_hint": payload.get("sub_state_hint", ""),
                "floor": payload.get("floor", ""),
                "confidence": payload.get("confidence"),
                "pose_x": payload.get("pose_x"),
                "pose_y": payload.get("pose_y"),
                "pose_yaw": payload.get("pose_yaw"),
                "patrol_epoch": payload.get("patrol_epoch"),
                "bbox": payload.get("bbox"),
                "source": "pc_frame_fetch",
            }
            entry = get_alert_store().add_alert(jpeg_bytes=jpeg, meta=meta)
            if entry:
                _LOG.info(
                    "person_event alert saved id=%s (%d bytes from %s)",
                    entry.get("id"),
                    len(jpeg),
                    frame_url,
                )
        except Exception as e:
            _LOG.warning("person_event alert fetch failed: %s url=%s", e, frame_url)

    threading.Thread(target=_worker, name="alert_frame_fetch", daemon=True).start()


def frame_url_for_debug() -> str:
    return _resolve_frame_url()
