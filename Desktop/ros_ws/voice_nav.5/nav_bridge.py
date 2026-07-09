"""MQTT room navigation (aligned with switcher_node)."""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Optional

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # type: ignore

TOPIC_NAV_ROOM = os.environ.get("VOICE_NAV_MQTT_TOPIC", "robot/nav_room")
TOPIC_NAV_CANCEL = os.environ.get("VOICE_NAV_MQTT_CANCEL_TOPIC", "robot/nav_cancel")
MQTT_BROKER = os.environ.get("VOICE_NAV_MQTT_BROKER", "broker.emqx.io")
MQTT_PORT = int(os.environ.get("VOICE_NAV_MQTT_PORT", "1883"))
ROBOT_ID = os.environ.get("VOICE_NAV_ROBOT_ID", "robot01")
STATUS_TOPIC = f"robot/{ROBOT_ID}/status"


class NavBridge:
    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._lock = threading.Lock()
        self._last_hb: dict[str, Any] = {}
        self._status_cb: Optional[Callable[[dict[str, Any]], None]] = None
        self._connected = False

    def set_status_callback(self, fn: Optional[Callable[[dict[str, Any]], None]]) -> None:
        self._status_cb = fn

    def _ensure_client(self) -> bool:
        if mqtt is None:
            print("[nav] paho-mqtt not installed (pip install paho-mqtt in Sherpa venv)", flush=True)
            return False
        with self._lock:
            if self._client is not None:
                if self._connected:
                    return True
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
                self._client = None
            c = mqtt.Client()
            c.on_connect = self._on_connect
            c.on_message = self._on_message
            try:
                c.connect(MQTT_BROKER, MQTT_PORT, 60)
                c.loop_start()
                self._client = c
            except Exception as exc:
                print(f"[nav] MQTT connect failed: {exc}", flush=True)
                self._client = None
                return False
            for _ in range(50):
                if self._connected:
                    return True
                if hasattr(c, "is_connected") and c.is_connected():
                    self._connected = True
                    c.subscribe(STATUS_TOPIC, 1)
                    return True
                time.sleep(0.2)
            return self._connected

    def _on_connect(self, client, userdata, flags, rc) -> None:
        self._connected = rc == 0
        if self._connected:
            client.subscribe(STATUS_TOPIC, 1)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return
        if msg.topic == STATUS_TOPIC:
            if payload.get("msg_type") == "robot_heartbeat":
                self._last_hb = payload
            if self._status_cb:
                self._status_cb(payload)

    @property
    def current_floor(self) -> str:
        return str(self._last_hb.get("current_floor", "?"))

    @property
    def nav_state(self) -> str:
        return str(self._last_hb.get("nav_state", ""))

    def ensure_monitoring(self) -> bool:
        """Subscribe MQTT status so nav_state is available for wake/stop logic."""
        return self._ensure_client()

    def navigate_room(self, room_id: str) -> tuple[bool, str]:
        room_id = str(room_id).strip()
        if not room_id:
            return False, "empty room id"
        if mqtt is None:
            return False, "缺少 paho-mqtt，请在 Sherpa 虚拟环境执行 pip install paho-mqtt"
        if not self._ensure_client():
            return False, (
                f"MQTT 未连接 {MQTT_BROKER}:{MQTT_PORT}，"
                "请确认 smart_switcher 已启动且 broker 可达"
            )
        try:
            info = self._client.publish(TOPIC_NAV_ROOM, room_id, qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return False, f"MQTT publish failed rc={info.rc}"
        except Exception as exc:
            return False, str(exc)
        return True, f"nav sent room {room_id} via {TOPIC_NAV_ROOM}"

    def cancel_navigation(self, reason: str = "voice") -> tuple[bool, str]:
        if mqtt is None:
            return False, "缺少 paho-mqtt"
        if not self._ensure_client():
            return False, f"MQTT 未连接 {MQTT_BROKER}:{MQTT_PORT}"
        payload = json.dumps({"reason": reason}, ensure_ascii=False)
        try:
            info = self._client.publish(TOPIC_NAV_CANCEL, payload, qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return False, f"MQTT publish failed rc={info.rc}"
        except Exception as exc:
            return False, str(exc)
        return True, f"nav cancel sent via {TOPIC_NAV_CANCEL}"

    def stop(self) -> None:
        with self._lock:
            c = self._client
            self._client = None
            self._connected = False
        if c is not None:
            try:
                c.loop_stop()
                c.disconnect()
            except Exception:
                pass
