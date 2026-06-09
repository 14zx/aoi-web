"""Тесты сквозной поддержки полигонов сегментации (маски YOLO-seg)."""

from __future__ import annotations

import numpy as np

from app.services.detector import DetectedDefect
from app.services.tilt_check import tilt_from_polygon
from app.services.visualization import render_result_image


def test_tilt_from_polygon_axis_aligned_small() -> None:
    poly = [(10, 10), (110, 10), (110, 40), (10, 40)]  # горизонтальный прямоугольник
    ang = tilt_from_polygon(poly)
    assert ang is not None and ang < 3.0


def test_tilt_from_polygon_rotated_large() -> None:
    # Прямоугольник, повёрнутый примерно на 30°.
    poly = [(0, 0), (87, 50), (72, 76), (-15, 26)]
    ang = tilt_from_polygon(poly)
    assert ang is not None and ang > 20.0


def test_tilt_from_polygon_degenerate_returns_none() -> None:
    assert tilt_from_polygon(None) is None
    assert tilt_from_polygon([(0, 0), (1, 1)]) is None  # < 3 точек


def test_parse_polygon_roundtrip() -> None:
    from app.api.inspections import _parse_polygon

    assert _parse_polygon(None) is None
    assert _parse_polygon("not-json") is None
    assert _parse_polygon("[[1,2],[3,4]]") is None  # < 3 точек
    assert _parse_polygon("[[1,2],[3,4],[5,6]]") == [[1, 2], [3, 4], [5, 6]]


def test_render_uses_polygon_without_crash() -> None:
    img = np.zeros((80, 120, 3), dtype=np.uint8)
    d = DetectedDefect(
        class_code="ic",
        class_name="ic",
        confidence=0.9,
        x1=10,
        y1=10,
        x2=70,
        y2=50,
        polygon=[(12, 12), (68, 14), (66, 48), (14, 46)],
    )
    out = render_result_image(img, [d])
    # Контур нарисован → внутри полигона появились ненулевые пиксели.
    assert out.shape == img.shape
    assert int(out.sum()) > 0


def test_detected_defect_polygon_defaults_none() -> None:
    d = DetectedDefect("ic", "ic", 0.5, 0, 0, 10, 10)
    assert d.polygon is None
