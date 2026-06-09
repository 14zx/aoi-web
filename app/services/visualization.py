"""Визуализация результата инспекции.

* ``render_result_image`` — полный кадр платы с рамками и подписями (для UI и PNG результата).
* ``render_masked_defect_protocol`` — только области дефектов на чёрном фоне (для ``masked.png`` в экспорте дообучения, п. ТЗ 4.8.4).
"""

from __future__ import annotations

import cv2
import numpy as np

from .detector import DEFECT_CLASSES, DetectedDefect


def _hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (b, g, r)


_COLOR_BY_CODE: dict[str, tuple[int, int, int]] = {
    c["code"]: _hex_to_bgr(c["color"]) for c in DEFECT_CLASSES
}

# Цвета для синтетических кодов постобработки (нет в основном реестре классов),
# чтобы рамки не сливались в зелёный fallback.
for _code, _hex in {
    "placement_tilt": "#FF00FF",
    "solder_bridge": "#E91E63",
    "golden_component_missing": "#FF1744",
    "golden_component_wrong": "#FF9100",
    "golden_polarity_wrong": "#00E5FF",
}.items():
    _COLOR_BY_CODE.setdefault(_code, _hex_to_bgr(_hex))


def _draw_outline(
    img: np.ndarray,
    defect: DetectedDefect,
    color: tuple[int, int, int],
    thickness: int,
) -> None:
    """Рисует контур сегментации (если есть) с полупрозрачной заливкой, иначе bbox."""
    poly = getattr(defect, "polygon", None)
    if poly and len(poly) >= 3:
        pts = np.asarray(poly, dtype=np.int32).reshape(-1, 1, 2)
        overlay = img.copy()
        cv2.fillPoly(overlay, [pts], color)
        cv2.addWeighted(overlay, 0.25, img, 0.75, 0, dst=img)
        cv2.polylines(
            img, [pts], isClosed=True, color=color, thickness=thickness, lineType=cv2.LINE_AA
        )
    else:
        cv2.rectangle(img, (defect.x1, defect.y1), (defect.x2, defect.y2), color, thickness)


def render_result_image(
    image_rgb: np.ndarray,
    defects: list[DetectedDefect],
    *,
    padding: int = 4,
) -> np.ndarray:
    """Полное изображение платы с нанесёнными рамками и подписями.

    Используется для сохранения ``results/*.png`` и отображения в веб-интерфейсе.
    Параметр ``padding`` оставлен для совместимости сигнатуры; рамки рисуются по bbox модели.
    """
    _ = padding
    h, w = image_rgb.shape[:2]
    bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    composed = bgr.copy()

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.5, min(h, w) / 1200.0)
    thickness = max(2, int(round(min(h, w) / 500.0)))

    for d in defects:
        color = _COLOR_BY_CODE.get(d.class_code, (0, 255, 0))
        _draw_outline(composed, d, color, thickness)

        label = f"{d.class_code}: {d.confidence:.2f}"
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        tx1, ty1 = d.x1, max(0, d.y1 - th - baseline - 4)
        tx2, ty2 = d.x1 + tw + 8, d.y1
        cv2.rectangle(composed, (tx1, ty1), (tx2, ty2), color, -1)
        cv2.putText(
            composed,
            label,
            (tx1 + 4, ty2 - baseline - 2),
            font,
            font_scale,
            (0, 0, 0),
            thickness,
            cv2.LINE_AA,
        )

    return composed


def render_masked_defect_protocol(
    image_rgb: np.ndarray,
    defects: list[DetectedDefect],
    *,
    padding: int = 4,
) -> np.ndarray:
    """Алгоритм ТЗ п. 4.8.4: маска из bbox, вне маски — чёрный, поверх — рамки и подписи.

    Нужен для ``training/.../masked.png`` (итоговый протокол для дообучения).
    """
    h, w = image_rgb.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    for d in defects:
        x1 = max(0, d.x1 - padding)
        y1 = max(0, d.y1 - padding)
        x2 = min(w, d.x2 + padding)
        y2 = min(h, d.y2 + padding)
        mask[y1:y2, x1:x2] = 1

    bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    black = np.zeros_like(bgr)
    composed = np.where(mask[:, :, None] == 1, bgr, black)

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.5, min(h, w) / 1200.0)
    thickness = max(2, int(round(min(h, w) / 500.0)))

    for d in defects:
        color = _COLOR_BY_CODE.get(d.class_code, (0, 255, 0))
        _draw_outline(composed, d, color, thickness)

        label = f"{d.class_code}: {d.confidence:.2f}"
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        tx1, ty1 = d.x1, max(0, d.y1 - th - baseline - 4)
        tx2, ty2 = d.x1 + tw + 8, d.y1
        cv2.rectangle(composed, (tx1, ty1), (tx2, ty2), color, -1)
        cv2.putText(
            composed,
            label,
            (tx1 + 4, ty2 - baseline - 2),
            font,
            font_scale,
            (0, 0, 0),
            thickness,
            cv2.LINE_AA,
        )

    return composed
