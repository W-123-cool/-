#!/usr/bin/env python3
import argparse
import cv2
import numpy as np
import yaml
from rknnlite.api import RKNNLite

def load_metadata(path):
    with open(path, "r", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    imgsz = m.get("imgsz", [640, 640])
    names = {int(k): str(v) for k, v in m.get("names", {}).items()}
    return (int(imgsz[0]), int(imgsz[1])), names

def letterbox(img, new_shape):
    h0, w0 = img.shape[:2]
    h, w = new_shape
    r = min(w / w0, h / h0)
    nw, nh = int(round(w0 * r)), int(round(h0 * r))
    dw, dh = (w - nw) / 2, (h - nh) / 2
    resized = cv2.resize(img, (nw, nh))
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    out = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114,114,114))
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

def postprocess(preds, conf_thres, iou_thres, ratio, dwdh, orig_shape):
    boxes_xywh = preds[:, :4]
    cls_scores = preds[:, 4:]
    cls_ids = np.argmax(cls_scores, axis=1)
    cls_conf = cls_scores[np.arange(cls_scores.shape[0]), cls_ids]
    keep = cls_conf >= conf_thres
    if not np.any(keep):
        return []

    boxes_xywh, cls_ids, cls_conf = boxes_xywh[keep], cls_ids[keep], cls_conf[keep]
    x, y, w, h = boxes_xywh[:,0], boxes_xywh[:,1], boxes_xywh[:,2], boxes_xywh[:,3]
    boxes = np.stack([x-w/2, y-h/2, x+w/2, y+h/2], axis=1).astype(np.float32)

    dw, dh = dwdh
    boxes[:,[0,2]] -= dw
    boxes[:,[1,3]] -= dh
    boxes /= ratio

    h0, w0 = orig_shape
    boxes[:,0] = np.clip(boxes[:,0], 0, w0-1)
    boxes[:,2] = np.clip(boxes[:,2], 0, w0-1)
    boxes[:,1] = np.clip(boxes[:,1], 0, h0-1)
    boxes[:,3] = np.clip(boxes[:,3], 0, h0-1)

    nms_boxes = [[float(b[0]), float(b[1]), float(max(0,b[2]-b[0])), float(max(0,b[3]-b[1]))] for b in boxes]
    idx = cv2.dnn.NMSBoxes(nms_boxes, cls_conf.tolist(), conf_thres, iou_thres)
    if len(idx) == 0:
        return []
    idx = idx.reshape(-1) if isinstance(idx, np.ndarray) else [i[0] if isinstance(i, (list, tuple)) else i for i in idx]

    dets = []
    for i in idx:
        x1, y1, x2, y2 = boxes[i]
        dets.append((int(cls_ids[i]), float(cls_conf[i]), (int(x1), int(y1), int(x2), int(y2))))
    return dets

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--source", default="/dev/video0")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    args = ap.parse_args()

    input_hw, names = load_metadata(args.meta)

    rknn = RKNNLite()
    assert rknn.load_rknn(args.model) == 0, "load_rknn failed"
    assert rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO) == 0, "init_runtime failed"

    src = int(args.source) if str(args.source).isdigit() else args.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"open camera failed: {args.source}")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        inp, ratio, dwdh = letterbox(frame, input_hw)
        inp = inp.transpose(2, 0, 1)              # HWC -> CHW
        inp = np.expand_dims(inp, axis=0)         # CHW -> NCHW
        inp = np.ascontiguousarray(inp, dtype=np.uint8)

        outs = rknn.inference(inputs=[inp], data_format=['nchw'])
        preds = reshape_output(outs)
        dets = postprocess(preds, args.conf, args.iou, ratio, dwdh, frame.shape[:2])

        for cls_id, score, (x1, y1, x2, y2) in dets:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0,255,0), 2)
            cv2.putText(frame, f"{names.get(cls_id, cls_id)} {score:.2f}", (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

        cv2.imshow("rknn_yolo11_cam", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    rknn.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
