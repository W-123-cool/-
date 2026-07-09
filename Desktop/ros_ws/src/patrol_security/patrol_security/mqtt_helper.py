"""Shared MQTT helpers for patrol_security nodes."""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

MQTT_BROKER = os.environ.get("MQTT_BROKER_HOST", "broker.emqx.io")
MQTT_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
ROBOT_ID = os.environ.get("MQTT_ROBOT_ID", "robot01").strip() or "robot01"


class PatrolMqttClient:
    def __init__(self, on_request: Optional[Callable[[dict], None]] = None) -> None:
        self._rid = ROBOT_ID
        self._topic_request = f"robot/{self._rid}/request"
        self._topic_status = f"robot/{self._rid}/status"
        self._topic_master = f"robot/{self._rid}/master/status"
        self._on_request = on_request
        self._master: dict[str, Any] = {}
        self._client: Optional[mqtt.Client] = None
        self._lock = threading.Lock()

    @property
    def master_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._master)

    def start(self) -> None:
        c = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION1)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.connect(MQTT_BROKER, MQTT_PORT, 60)
        c.loop_start()
        self._client = c

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc not in (0, None):
            return
        client.subscribe(self._topic_request, 1)
        client.subscribe(self._topic_master, 1)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        mt = str(data.get("msg_type", "")).strip()
        if msg.topic == self._topic_master or mt == "master_status":
            with self._lock:
                self._master = data
            return
        if msg.topic == self._topic_request and self._on_request:
            self._on_request(data)

    def publish_status(self, obj: dict[str, Any]) -> None:
        c = self._client
        if c is None:
            return
        try:
            c.publish(self._topic_status, json.dumps(obj, ensure_ascii=False), qos=1)
        except Exception:
            pass
