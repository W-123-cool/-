"""MJPEG HTTP server for patrol camera live preview."""
from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional

import cv2
import numpy as np


class LiveFrameBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpg: Optional[bytes] = None
        self._seq: int = 0

    def set_jpeg(self, data: bytes) -> None:
        with self._lock:
            self._jpg = data
            self._seq += 1

    def get_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._jpg

    def get_jpeg_seq(self) -> tuple[Optional[bytes], int]:
        with self._lock:
            return self._jpg, self._seq


def start_mjpeg_server(
    buffer: LiveFrameBuffer,
    *,
    host: str = "0.0.0.0",
    port: int = 8089,
    logger: Optional[Callable[[str], None]] = None,
) -> HTTPServer:
    log = logger or (lambda _msg: None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/frame.jpg", "/frame"):
                jpg = buffer.get_jpeg()
                if not jpg:
                    self.send_error(503, "no frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self._cors()
                self.end_headers()
                self.wfile.write(jpg)
                return
            if path not in ("/", "/stream", "/stream/"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self._cors()
            self.end_headers()
            try:
                min_interval = float(os.environ.get("PATROL_MJPEG_MIN_INTERVAL", "0.033"))
                last_seq = -1
                while True:
                    jpg, seq = buffer.get_jpeg_seq()
                    if jpg and seq != last_seq:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                        last_seq = seq
                    time.sleep(min_interval)
            except (BrokenPipeError, ConnectionResetError):
                pass

    class ReuseHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReuseHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="patrol_mjpeg").start()
    log(f"MJPEG live stream http://{host}:{port}/stream (frame.jpg for img poll)")
    return server


def frame_to_jpeg(frame: np.ndarray, *, max_width: int = 640, quality: int = 70) -> Optional[bytes]:
    vis = frame
    h, w = vis.shape[:2]
    if w > max_width:
        vis = cv2.resize(vis, (max_width, int(h * max_width / w)))
    ok, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return None
    return buf.tobytes()
