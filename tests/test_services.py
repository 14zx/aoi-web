"""Тесты сервисов предобработки и визуализации."""

from __future__ import annotations

import numpy as np

from app.services.detector import DEFECT_CLASSES, DetectedDefect
from app.services.preprocessing import ImageValidationError, load_image, preprocess_image
from app.services.visualization import render_masked_defect_protocol, render_result_image


def test_defect_classes_cover_tz():
    """Базовый справочник в detector.py — шесть якорных кодов (совместимость с DeepPCB-стилем)."""
    codes = {c["code"] for c in DEFECT_CLASSES}
    assert codes == {"open", "short", "mousebite", "spur", "copper", "pinhole"}


def test_load_image_rejects_small(tmp_path):
    import cv2
    img = np.full((200, 200, 3), 255, np.uint8)
    p = tmp_path / "s.png"
    cv2.imwrite(str(p), img)
    try:
        load_image(p)
    except ImageValidationError as e:
        assert "Минимальное" in str(e)
    else:  # pragma: no cover
        raise AssertionError("Должно быть отклонено")


def test_preprocess_preserves_shape():
    img = (np.random.rand(700, 700, 3) * 255).astype(np.uint8)
    out = preprocess_image(img)
    assert out.shape == img.shape
    assert out.dtype == np.uint8


def test_render_shows_full_board_with_boxes():
    img = np.full((400, 400, 3), 200, np.uint8)
    defect = DetectedDefect(
        class_code="short",
        class_name="Короткое замыкание",
        confidence=0.9,
        x1=100, y1=100, x2=200, y2=200,
    )
    out = render_result_image(img, [defect])
    # Угол кадра вне bbox — не затирается в чёрный: остаётся исходная яркость (BGR).
    corner = tuple(int(v) for v in out[10, 10])
    assert corner == (200, 200, 200)
    centre = tuple(int(v) for v in out[150, 150])
    assert not (centre[0] == 0 and centre[1] == 0 and centre[2] == 0)


def test_masked_protocol_black_outside_defects():
    img = np.full((400, 400, 3), 200, np.uint8)
    defect = DetectedDefect(
        class_code="short",
        class_name="Короткое замыкание",
        confidence=0.9,
        x1=100, y1=100, x2=200, y2=200,
    )
    out = render_masked_defect_protocol(img, [defect])
    assert tuple(int(v) for v in out[10, 10]) == (0, 0, 0)
    centre = out[150, 150]
    assert not (centre[0] == 0 and centre[1] == 0 and centre[2] == 0)
