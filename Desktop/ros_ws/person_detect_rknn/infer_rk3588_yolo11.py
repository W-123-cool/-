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
    if len(imgsz) != 2:
        raise ValueError(f"Invalid imgsz in metadata: {imgsz}")

    names_raw = meta.get("names", {})
    names: Dict[int, str] = {}
    for k, v in names_raw.items():
        names[int(k)] = str(v)
    return (int(imgsz[0]), int(imgsz[1])), names


def letterbox(image: np.ndarray, new_shape: Tuple[int, int]) -> Tuple[np.ndarray, float, Tuple[float, float]]:
    shape = image.shape[:2]  # (h, w)
    h_new, w_new = new_shape
    ratio = min(w_new / shape[1], h_new / shape[0])

    new_unpad = (int(round(shape[1] * ratio)), int(round(shape[0] * ratio)))  # (w, h)
    dw = (w_new - new_unpad[0]) / 2
    dh = (h_new - new_unpad[1]) / 2

    resized = cv2.resize(image, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return padded, ratio, (dw, dh)


def _reshape_yolo_output(outputs: Sequence[np.ndarray]) -> np.ndarray:
    if not outputs:
        raise RuntimeError("RKNN inference returned no outputs.")

    best = None
    for out in outputs:
        arr = np.array(out)
        if arr.ndim == 3:
            if arr.shape[0] == 1 and arr.shape[1] >= 6 and arr.shape[2] >= 1:
                best = arr[0]
                break
            if arr.shape[0] == 1 and arr.shape[2] >= 6 and arr.shape[1] >= 1:
                best = arr[0].T
                break
        elif arr.ndim == 2:
            if arr.shape[1] >= 6:
                best = arr
                break
            if arr.shape[0] >= 6:
                best = arr.T
                break

    if best is None:
        shapes = [tuple(np.array(o).shape) for o in outputs]
        raise RuntimeError(f"Unsupported RKNN output layout: {shapes}")

    # Ensure [num_boxes, num_channels]
    if best.shape[0] < best.shape[1] and best.shape[0] <= 128:
        best = best.T
    return best.astype(np.float32, copy=False)


def postprocess(
    preds: np.ndarray,
    conf_thres: float,
    iou_thres: float,
    ratio: float,
    dwdh: Tuple[float, float],
    orig_shape: Tuple[int, int],
) -> List[Tuple[int, float, Tuple[int, int, int, int]]]:
    if preds.shape[1] < 6:
        return []

    boxes_xywh = preds[:, :4]
    cls_scores = preds[:, 4:]
    cls_ids = np.argmax(cls_scores, axis=1)
    cls_conf = cls_scores[np.arange(cls_scores.shape[0]), cls_ids]

    keep = cls_conf >= conf_thres
    if not np.any(keep):
        return []

    boxes_xywh = boxes_xywh[keep]
    cls_ids = cls_ids[keep]
    cls_conf = cls_conf[keep]

    # xywh -> xyxy in model input space.
    x, y, w, h = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
    x1 = x - w / 2
    y1 = y - h / 2
    x2 = x + w / 2
    y2 = y + h / 2
    boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1).astype(np.float32)

    # Undo letterbox.
    dw, dh = dwdh
    boxes_xyxy[:, [0, 2]] -= dw
    boxes_xyxy[:, [1, 3]] -= dh
    boxes_xyxy /= ratio

    h0, w0 = orig_shape
    boxes_xyxy[:, 0] = np.clip(boxes_xyxy[:, 0], 0, w0 - 1)
    boxes_xyxy[:, 1] = np.clip(boxes_xyxy[:, 1], 0, h0 - 1)
    boxes_xyxy[:, 2] = np.clip(boxes_xyxy[:, 2], 0, w0 - 1)
    boxes_xyxy[:, 3] = np.clip(boxes_xyxy[:, 3], 0, h0 - 1)

    nms_boxes = []
    for b in boxes_xyxy:
        nms_boxes.append([float(b[0]), float(b[1]), float(max(0.0, b[2] - b[0])), float(max(0.0, b[3] - b[1]))])

    indices = cv2.dnn.NMSBoxes(nms_boxes, cls_conf.tolist(), conf_thres, iou_thres)
    if len(indices) == 0:
        return []

    if isinstance(indices, np.ndarray):
        keep_idx = indices.reshape(-1).tolist()
    else:
        keep_idx = [i[0] if isinstance(i, (list, tuple, np.ndarray)) else i for i in indices]

    dets = []
    for i in keep_idx:
        x1i, y1i, x2i, y2i = boxes_xyxy[i]
        dets.append((int(cls_ids[i]), float(cls_conf[i]), (int(x1i), int(y1i), int(x2i), int(y2i))))
    return dets


def draw_detections(image: np.ndarray, dets: Sequence[Tuple[int, float, Tuple[int, int, int, int]]], names: Dict[int, str]) -> np.ndarray:
    out = image.copy()
    for cls_id, score, (x1, y1, x2, y2) in dets:
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{names.get(cls_id, str(cls_id))} {score:.2f}"
        cv2.putText(out, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="YOLO11 RKNN image inference on RK3588.")
    parser.add_argument("--model", required=True, help="Path to .rknn model file.")
    parser.add_argument("--meta", required=True, help="Path to metadata.yaml.")
    parser.add_argument("--image", required=True, help="Input image path.")
    parser.add_argument("--output", default="result.jpg", help="Output image path with boxes.")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold.")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU threshold.")
    args = parser.parse_args()

    model_path = Path(args.model)
    meta_path = Path(args.meta)
    image_path = Path(args.image)
    output_path = Path(args.output)

    input_hw, names = load_metadata(meta_path)
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Failed to read image: {image_path}")

    inp, ratio, dwdh = letterbox(image, input_hw)

    rknn = RKNNLite()
    ret = rknn.load_rknn(str(model_path))
    if ret != 0:
        raise RuntimeError(f"load_rknn failed, ret={ret}")

    ret = rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO)
    if ret != 0:
        raise RuntimeError(f"init_runtime failed, ret={ret}")

    outputs = rknn.inference(inputs=[inp])
    rknn.release()

    preds = _reshape_yolo_output(outputs)
    dets = postprocess(preds, args.conf, args.iou, ratio, dwdh, image.shape[:2])
    vis = draw_detections(image, dets, names)
    cv2.imwrite(str(output_path), vis)

    print(f"[INFO] detections: {len(dets)}")
    for cls_id, score, box in dets:
        print(f"  - class={cls_id} ({names.get(cls_id, 'unknown')}), conf={score:.3f}, box={box}")
    print(f"[INFO] saved: {output_path}")


if __name__ == "__main__":
    main()
