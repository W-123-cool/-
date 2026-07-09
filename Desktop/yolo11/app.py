#!/usr/bin/env python3
import os
import cv2
import csv
import yaml
import time
import numpy as np
from datetime import datetime
from threading import Thread, Lock
from flask import Flask, Response, jsonify, render_template, send_from_directory, request
from rknnlite.api import RKNNLite

# ========= 配置 =========
MODEL_PATH = "./yolo11n-rk3588.rknn"
META_PATH = "./metadata.yaml"
# UVC: PC Camera A4 → /dev/video2；Orbbec Astra → /dev/video0（勿用于本脚本）
VIDEO_SOURCE = os.environ.get("VIDEO_SOURCE", "/dev/video2")
CONF_THRES = 0.25
IOU_THRES = 0.45
CAPTURE_COOLDOWN_SEC = 60         # 每分钟最多抓拍一次
CAPTURE_DIR = "./captures"

app = Flask(__name__)
os.makedirs(CAPTURE_DIR, exist_ok=True)

state_lock = Lock()
latest_jpeg = None
person_detected = False
last_capture_ts = 0.0
last_warning_ts = 0.0
running = True


def load_metadata(path):
    with open(path, "r", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    imgsz = m.get("imgsz", [640, 640])
    return (int(imgsz[0]), int(imgsz[1]))


def letterbox(img, new_shape):
    h0, w0 = img.shape[:2]
    h, w = new_shape
    r = min(w / w0, h / h0)
    nw, nh = int(round(w0 * r)), int(round(h0 * r))
    dw, dh = (w - nw) / 2, (h - nh) / 2
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    out = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return out, r, (dw, dh)


def reshape_output(outputs):
    if outputs is None or len(outputs) == 0:
        raise RuntimeError("RKNN inference returned empty outputs")
    arr = np.array(outputs[0])
    if arr.ndim == 3 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 2 and arr.shape[0] < arr.shape[1] and arr.shape[0] <= 128:
        arr = arr.T
    if arr.ndim != 2 or arr.shape[1] < 6:
        raise RuntimeError(f"Unsupported output shape: {arr.shape}")
    return arr.astype(np.float32)


def postprocess_person_only(preds, conf_thres, iou_thres, ratio, dwdh, orig_shape):
    # 只检测 person：COCO 类别 0，对应 preds[:, 4]
    person_conf = preds[:, 4]
    keep = person_conf >= conf_thres
    if not np.any(keep):
        return []

    boxes_xywh = preds[keep, :4]
    confs = person_conf[keep]

    x, y, w, h = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
    boxes = np.stack([x - w / 2, y - h / 2, x + w / 2, y + h / 2], axis=1).astype(np.float32)

    dw, dh = dwdh
    boxes[:, [0, 2]] -= dw
    boxes[:, [1, 3]] -= dh
    boxes /= ratio

    h0, w0 = orig_shape
    boxes[:, 0] = np.clip(boxes[:, 0], 0, w0 - 1)
    boxes[:, 2] = np.clip(boxes[:, 2], 0, w0 - 1)
    boxes[:, 1] = np.clip(boxes[:, 1], 0, h0 - 1)
    boxes[:, 3] = np.clip(boxes[:, 3], 0, h0 - 1)

    nms_boxes = [[float(b[0]), float(b[1]), float(max(0, b[2] - b[0])), float(max(0, b[3] - b[1]))] for b in boxes]
    idx = cv2.dnn.NMSBoxes(nms_boxes, confs.tolist(), conf_thres, iou_thres)
    if len(idx) == 0:
        return []

    idx = idx.reshape(-1) if isinstance(idx, np.ndarray) else [i[0] if isinstance(i, (list, tuple)) else i for i in idx]
    dets = []
    for i in idx:
        x1, y1, x2, y2 = boxes[i]
        dets.append((float(confs[i]), (int(x1), int(y1), int(x2), int(y2))))
    return dets


def list_captures_all():
    items = []
    for name in os.listdir(CAPTURE_DIR):
        p = os.path.join(CAPTURE_DIR, name)
        if not os.path.isfile(p):
            continue
        if not name.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        ts = os.path.getmtime(p)
        items.append({
            "file": name,
            "ts": ts,
            "time_str": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            "url": f"/captures/{name}",
        })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


def detector_loop():
    global latest_jpeg, person_detected, last_capture_ts, last_warning_ts, running

    input_hw = load_metadata(META_PATH)

    rknn = RKNNLite()
    if rknn.load_rknn(MODEL_PATH) != 0:
        raise RuntimeError("load_rknn failed")
    if rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO) != 0:
        raise RuntimeError("init_runtime failed")

    cap = cv2.VideoCapture(VIDEO_SOURCE, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"open camera failed: {VIDEO_SOURCE}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    print(f"[detector] camera={VIDEO_SOURCE} 640x480", flush=True)

    while running:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.02)
            continue

        inp, ratio, dwdh = letterbox(frame, input_hw)
        inp = np.expand_dims(inp, axis=0)  # HWC -> NHWC（RKNN 模型要求 NHWC）
        inp = np.ascontiguousarray(inp, dtype=np.uint8)

        outs = rknn.inference(inputs=[inp], data_format=["nhwc"])
        preds = reshape_output(outs)
        dets = postprocess_person_only(preds, CONF_THRES, IOU_THRES, ratio, dwdh, frame.shape[:2])

        now = time.time()
        has_person = len(dets) > 0

        for score, (x1, y1, x2, y2) in dets:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(frame, f"person {score:.2f}", (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        if has_person:
            cv2.putText(frame, "WARNING: PERSON DETECTED", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            last_warning_ts = now

            if now - last_capture_ts >= CAPTURE_COOLDOWN_SEC:
                fname = datetime.now().strftime("capture_%Y%m%d_%H%M%S.jpg")
                cv2.imwrite(os.path.join(CAPTURE_DIR, fname), frame)
                last_capture_ts = now

        ret, buf = cv2.imencode(".jpg", frame)
        if ret:
            with state_lock:
                latest_jpeg = buf.tobytes()
                person_detected = has_person

    cap.release()
    rknn.release()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    def gen():
        while True:
            with state_lock:
                data = latest_jpeg
            if data is None:
                time.sleep(0.03)
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
            time.sleep(0.03)
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def api_status():
    now = time.time()
    with state_lock:
        detected = person_detected
        warn_ts = last_warning_ts
    return jsonify({
        "person_detected": detected,
        "warning_active": detected or (now - warn_ts < 2.0),
        "cooldown_sec": CAPTURE_COOLDOWN_SEC
    })


@app.route("/api/captures")
def api_captures():
    page = max(int(request.args.get("page", 1)), 1)
    page_size = max(min(int(request.args.get("page_size", 10)), 100), 1)
    items = list_captures_all()
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return jsonify({
        "items": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    })


@app.route("/api/captures/export_csv")
def api_export_csv():
    items = list_captures_all()
    out_path = os.path.join(CAPTURE_DIR, "captures_history.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "time_str", "timestamp", "url"])
        for it in items:
            w.writerow([it["file"], it["time_str"], it["ts"], it["url"]])
    return jsonify({"ok": True, "file": "captures_history.csv", "url": "/captures/captures_history.csv"})


@app.route("/api/captures/delete", methods=["POST"])
def api_delete_capture():
    data = request.get_json(silent=True) or {}
    file_name = data.get("file", "").strip()
    if not file_name:
        return jsonify({"ok": False, "msg": "missing file"}), 400
    full = os.path.join(CAPTURE_DIR, file_name)
    if not os.path.isfile(full):
        return jsonify({"ok": False, "msg": "file not found"}), 404
    os.remove(full)
    return jsonify({"ok": True})


@app.route("/api/captures/clear", methods=["POST"])
def api_clear_captures():
    cnt = 0
    for name in os.listdir(CAPTURE_DIR):
        p = os.path.join(CAPTURE_DIR, name)
        if os.path.isfile(p) and name.lower().endswith((".jpg", ".jpeg", ".png")):
            os.remove(p)
            cnt += 1
    return jsonify({"ok": True, "deleted": cnt})


@app.route("/captures/<path:filename>")
def get_capture(filename):
    return send_from_directory(CAPTURE_DIR, filename)


if __name__ == "__main__":
    t = Thread(target=detector_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
