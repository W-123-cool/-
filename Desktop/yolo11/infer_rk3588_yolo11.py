#!/usr/bin/env python3
import argparse
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import cv2
import numpy as np
import yaml
from rknnlite.api import RKNNLite


def load_metadata(path: Path) -> Tuple[Tuple[int, int], Dict[int, str]]:
    with path.open("r", encoding="utf-8") as f:
        meta = yaml.safe_load(f)
    imgsz = meta.get("imgsz", [640, 640])
    names_raw = meta.get("names", {})
    names = {int(k): str(v) for k, v in names_raw.items()}
    return (int(imgsz[0]), int(imgsz[1])), names


def letterbox(image: np.ndarray, new_shape: Tuple[int, int]):
    shape = image.shape[:2]  # h,w
    h_new, w_new = new_shape
    ratio = min(w_new / shape[1], h_new / shape[0])
    new_unpad = (int(round(shape[1] * ratio)), int(round(shape[0] * ratio)))  # w,h
    dw, dh = (w_new - new_unpad[0]) / 2, (h_new - new_unpad[1]) / 2
    resized = cv2.resize(image, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return padded, ratio, (dw, dh)


def reshape_output(outputs: Sequence[np.ndarray]) -> np.ndarray:
    if not outputs:
        raise RuntimeError("No RKNN outputs")
    arr = np.array(outputs[0])
    if arr.ndim == 3 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 2 and arr.shape[0] < arr.shape[1] and arr.shape[0] <= 128:
        arr = arr.T
    if arr.ndim != 2 or arr.shape[1] < 6:
        raise RuntimeError(f"Unsupported output shape: {arr.shape}")
    return arr.astype(np.float32, copy=False)


def postprocess(preds, conf_thres, iou_thres, ratio, dwdh, orig_shape):
    boxes_xywh = preds[:, :4]
    cls_scores = preds[:, 4:]
    cls_ids = np.argmax(cls_scores, axis=1)
    cls_conf = cls_scores[np.arange(cls_scores.shape[0]), cls_ids]
    keep = cls_conf >= conf_thres
    if not np.any(keep):
        return []

    boxes_xywh, cls_ids, cls_conf = boxes_xywh[keep], cls_ids[keep], cls_conf[keep]
    x, y, w, h = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
    boxes = np.stack([x - w / 2, y - h / 2, x + w / 2, y + h / 2], axis=1).astype(np.float32)

    dw, dh = dwdh
    boxes[:, [0, 2]] -= dw
    boxes[:, [1, 3]] -= dh
    boxes /= ratio

    h0, w0 = orig_shape
    boxes[:, 0] = np.clip(boxes[:, 0], 0, w0 - 1)
    boxes[:, 1] = np.clip(boxes[:, 1], 0, h0 - 1)
    boxes[:, 2] = np.clip(boxes[:, 2], 0, w0 - 1)
    boxes[:, 3] = np.clip(boxes[:, 3], 0, h0 - 1)

    nms_boxes = [[float(b[0]), float(b[1]), float(max(0, b[2] - b[0])), float(max(0, b[3] - b[1]))] for b in boxes]
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
    ap.add_argument("--image", required=True)
    ap.add_argument("--output", default="result.jpg")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    args = ap.parse_args()

    input_hw, names = load_metadata(Path(args.meta))
    image = cv2.imread(args.image)
    if image is None:
        raise FileNotFoundError(args.image)

    inp, ratio, dwdh = letterbox(image, input_hw)

    rknn = RKNNLite()
    assert rknn.load_rknn(args.model) == 0, "load_rknn failed"
    assert rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO) == 0, "init_runtime failed"
    outputs = rknn.inference(inputs=[inp])
    rknn.release()

    preds = reshape_output(outputs)
    dets = postprocess(preds, args.conf, args.iou, ratio, dwdh, image.shape[:2])

    vis = image.copy()
    for cls_id, score, (x1, y1, x2, y2) in dets:
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(vis, f"{names.get(cls_id, cls_id)} {score:.2f}", (x1, max(20, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    cv2.imwrite(args.output, vis)
    print(f"[INFO] detections: {len(dets)}")
    print(f"[INFO] saved: {args.output}")


if __name__ == "__main__":
    main()
