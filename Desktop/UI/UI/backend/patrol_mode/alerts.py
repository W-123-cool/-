"""P1c person-detection alert storage."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from patrol_mode.config import (
    ALERT_DEDUP_SEC,
    ALERT_MAX_COUNT,
    ALERT_RETENTION_DAYS,
    alerts_dir,
    alerts_index_path,
)


class AlertStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._unread = 0

    def _load_index(self) -> list[dict[str, Any]]:
        p = alerts_index_path()
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return list(data) if isinstance(data, list) else []
        except Exception:
            return []

    def _save_index(self, items: list[dict[str, Any]]) -> None:
        alerts_index_path().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _prune(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = time.time() - ALERT_RETENTION_DAYS * 86400
        kept = [x for x in items if float(x.get("ts", 0)) >= cutoff]
        kept.sort(key=lambda x: float(x.get("ts", 0)), reverse=True)
        if len(kept) > ALERT_MAX_COUNT:
            for drop in kept[ALERT_MAX_COUNT:]:
                img = drop.get("image_path")
                if img:
                    try:
                        Path(str(img)).unlink(missing_ok=True)
                    except Exception:
                        pass
            kept = kept[:ALERT_MAX_COUNT]
        return kept

    def add_alert(
        self,
        *,
        jpeg_bytes: bytes,
        meta: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            if ALERT_DEDUP_SEC > 0:
                cutoff = time.time() - ALERT_DEDUP_SEC
                for x in self._load_index()[:8]:
                    if float(x.get("ts", 0)) >= cutoff:
                        return x
        alert_id = uuid.uuid4().hex[:16]
        ts = time.time()
        fname = f"{alert_id}.jpg"
        img_path = alerts_dir() / fname
        img_path.write_bytes(jpeg_bytes)
        entry = {
            "id": alert_id,
            "ts": ts,
            "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "image_path": str(img_path),
            "image_url": f"/api/security/alerts/{alert_id}/image",
            "mode": str(meta.get("mode", "") or meta.get("sub_state_hint", "")),
            "floor": meta.get("floor", ""),
            "confidence": meta.get("confidence"),
            "bbox": meta.get("bbox"),
            "pose_x": meta.get("pose_x"),
            "pose_y": meta.get("pose_y"),
            "pose_yaw": meta.get("pose_yaw"),
            "patrol_epoch": meta.get("patrol_epoch"),
            "nearest_wp_index": meta.get("nearest_wp_index"),
            "nearest_wp_label": meta.get("nearest_wp_label"),
            "source": meta.get("source", "vehicle"),
        }
        with self._lock:
            items = self._prune(self._load_index())
            items.insert(0, entry)
            self._save_index(items)
            self._unread += 1
        return entry

    def list_alerts(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._lock:
            items = self._load_index()
            total = len(items)
            page = items[offset : offset + limit]
            unread = self._unread
        return {
            "total": total,
            "unread": unread,
            "items": [
                {k: v for k, v in x.items() if k != "image_path"}
                for x in page
            ],
        }

    def get_image_path(self, alert_id: str) -> Optional[Path]:
        with self._lock:
            for x in self._load_index():
                if str(x.get("id")) == alert_id:
                    p = Path(str(x.get("image_path", "")))
                    return p if p.is_file() else None
        return None

    def mark_read(self) -> int:
        with self._lock:
            n = self._unread
            self._unread = 0
            return n

    def unread_count(self) -> int:
        with self._lock:
            return self._unread

    def delete_alert(self, alert_id: str) -> tuple[bool, str]:
        alert_id = str(alert_id or "").strip()
        if not alert_id:
            return False, "缺少 alert_id"
        with self._lock:
            items = self._load_index()
            kept: list[dict[str, Any]] = []
            removed: dict[str, Any] | None = None
            for x in items:
                if str(x.get("id")) == alert_id:
                    removed = x
                else:
                    kept.append(x)
            if removed is None:
                return False, "告警不存在"
            img = removed.get("image_path")
            if img:
                try:
                    Path(str(img)).unlink(missing_ok=True)
                except Exception:
                    pass
            self._save_index(kept)
        return True, "已删除"

    def delete_all(self) -> tuple[int, str]:
        with self._lock:
            items = self._load_index()
            n = len(items)
            for x in items:
                img = x.get("image_path")
                if img:
                    try:
                        Path(str(img)).unlink(missing_ok=True)
                    except Exception:
                        pass
            self._save_index([])
            self._unread = 0
        return n, f"已删除 {n} 条告警"

    def total_count(self) -> int:
        with self._lock:
            return len(self._load_index())


_store = AlertStore()


def get_alert_store() -> AlertStore:
    return _store
