"""Select the nearest person from detections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PersonDetection:
    score: float
    bbox: tuple[int, int, int, int]

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)

    @property
    def center_x(self) -> float:
        x1, _, x2, _ = self.bbox
        return (x1 + x2) / 2.0

    @property
    def bottom_y(self) -> int:
        return self.bbox[3]


def detections_to_persons(
    detections: list[tuple[int, int, int, int, float]],
) -> list[PersonDetection]:
    return [PersonDetection(score=score, bbox=(x1, y1, x2, y2)) for x1, y1, x2, y2, score in detections]


def select_nearest(persons: list[PersonDetection]) -> Optional[PersonDetection]:
    """Pick the person closest to the camera (largest bbox bottom y / nearest screen bottom)."""
    if not persons:
        return None
    return max(persons, key=lambda person: person.bottom_y)
