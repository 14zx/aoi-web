"""Тесты эвристики оценки наклона компонента (tilt_check)."""

from __future__ import annotations

import cv2
import numpy as np

from app.services.tilt_check import max_axis_tilt_degrees


def _blank(h: int = 120, w: int = 120) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_axis_aligned_rectangle_has_small_tilt() -> None:
    img = _blank()
    cv2.rectangle(img, (20, 50), (100, 70), (230, 230, 230), -1)
    ang = max_axis_tilt_degrees(img)
    assert ang is not None
    assert ang < 8.0


def test_strongly_tilted_rectangle_flagged() -> None:
    img = _blank(160, 160)
    rect = ((80, 80), (110, 28), 30.0)  # повёрнут на 30°
    box = cv2.boxPoints(rect).astype(np.int32)
    cv2.fillPoly(img, [box], (235, 235, 235))
    ang = max_axis_tilt_degrees(img)
    assert ang is not None
    assert ang > 20.0


def test_near_square_returns_none() -> None:
    img = _blank()
    cv2.rectangle(img, (40, 40), (80, 82), (230, 230, 230), -1)  # почти квадрат
    assert max_axis_tilt_degrees(img) is None


def test_empty_or_tiny_crop_returns_none() -> None:
    assert max_axis_tilt_degrees(None) is None
    assert max_axis_tilt_degrees(np.zeros((5, 5, 3), dtype=np.uint8)) is None
