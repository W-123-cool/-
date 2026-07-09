#!/usr/bin/env python3
import argparse
from collections import deque
from typing import Optional

import cv2


class PersonDetector:
    """Simple detector wrapper with YOLO first, HOG fallback."""

    def __init__(self, yolo_model: Optional[str] = None, conf_thres: float = 0.25):
        self.conf_thres = conf_thres
        self.model = None
        self.use_hog = False

        if yolo_model:
            try:
                from ultralytics import YOLO  # type: ignore

                self.model = YOLO(yolo_model)
                print(f"[INFO] YOLO model loaded: {yolo_model}")
            except Exception as exc:
                print(f"[WARN] YOLO load failed, fallback to HOG: {exc}")
                self._init_hog()
        else:
            self._init_hog()

    def _init_hog(self) -> None:
        self.use_hog = True
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        print("[INFO] Using OpenCV HOG person detector.")

    def has_person(self, frame) -> bool:
        if self.use_hog:
            rects, _ = self.hog.detectMultiScale(
                frame,
                winStride=(8, 8),
                padding=(8, 8),
                scale=1.05,
            )
            return len(rects) > 0

        # YOLO path: class 0 is "person" in COCO models.
        results = self.model.predict(frame, conf=self.conf_thres, verbose=False)
        if not results:
            return False

        boxes = results[0].boxes
        if boxes is None or boxes.cls is None:
            return False
        return any(int(c.item()) == 0 for c in boxes.cls)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Video person detection with 30-frame sliding window."
    )
    parser.add_argument("--source", default=0, help="Camera index or video/rtsp url.")
    parser.add_argument("--window-size", type=int, default=30, help="Sliding window size.")
    parser.add_argument(
        "--person-threshold",
        type=int,
        default=10,
        help="If person frames >= this value in window, print once.",
    )
    parser.add_argument(
        "--cooldown-frames",
        type=int,
        default=30,
        help="Cooldown frames before printing again.",
    )
    parser.add_argument("--yolo-model", default="", help="Optional YOLO model path (.pt/.onnx).")
    parser.add_argument("--conf", type=float, default=0.25, help="YOLO confidence threshold.")
    parser.add_argument("--show", action="store_true", help="Show preview window.")
    args = parser.parse_args()

    source = int(args.source) if str(args.source).isdigit() else args.source
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open source: {args.source}")

    detector = PersonDetector(
        yolo_model=args.yolo_model if args.yolo_model else None,
        conf_thres=args.conf,
    )
    window = deque(maxlen=args.window_size)
    cooldown = 0

    print(
        f"[INFO] Start detect: window={args.window_size}, threshold={args.person_threshold}, "
        f"cooldown={args.cooldown_frames}"
    )
    while True:
        ok, frame = cap.read()
        if not ok:
            print("[INFO] Stream ended or failed to read frame.")
            break

        person_now = detector.has_person(frame)
        window.append(1 if person_now else 0)
        person_count = sum(window)

        if cooldown > 0:
            cooldown -= 1

        if len(window) == args.window_size and person_count >= args.person_threshold and cooldown == 0:
            print("有人")
            cooldown = args.cooldown_frames

        if args.show:
            text = f"person_frames={person_count}/{len(window)}"
            cv2.putText(frame, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.imshow("person_detect_window", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
