"""Постобработка детекций: контроль угла установки компонентов (по разметке «component»)."""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from .class_semantics import is_component_class, load_mappings
from .detector import DetectedDefect
from .tilt_check import max_axis_tilt_degrees, tilt_from_polygon

logger = logging.getLogger(__name__)


def apply_component_tilt_rules(
    rgb: np.ndarray,
    defects: list[DetectedDefect],
    db: Session,
) -> list[DetectedDefect]:
    """Для классов, отмеченных как «component», при наклоне > порога добавляет дефект ``placement_tilt``."""
    mappings = load_mappings(db)
    if not mappings:
        return defects

    h, w = rgb.shape[:2]
    pad = 16
    extra: list[DetectedDefect] = []
    max_deg = float(settings.component_tilt_max_deg)

    for d in defects:
        if not is_component_class(d.class_code, mappings):
            continue
        x1 = max(0, d.x1 - pad)
        y1 = max(0, d.y1 - pad)
        x2 = min(w, d.x2 + pad)
        y2 = min(h, d.y2 + pad)
        if x2 <= x1 + 2 or y2 <= y1 + 2:
            continue
        # Контур сегментации (если модель YOLO-seg) даёт ориентацию точнее,
        # чем Otsu по кропу bbox. При его отсутствии — старая эвристика.
        ang = tilt_from_polygon(d.polygon)
        if ang is None:
            crop = rgb[y1:y2, x1:x2]
            ang = max_axis_tilt_degrees(crop)
        if ang is None:
            continue
        if ang <= max_deg:
            continue
        extra.append(
            DetectedDefect(
                class_code="placement_tilt",
                class_name=f"Нарушение ориентации (≈{ang:.0f}°)",
                confidence=float(min(1.0, max(0.4, ang / 90.0))),
                x1=d.x1,
                y1=d.y1,
                x2=d.x2,
                y2=d.y2,
                polygon=d.polygon,
            )
        )
        logger.debug(
            "placement_tilt for component class=%s angle=%.1f bbox=%s,%s,%s,%s",
            d.class_code,
            ang,
            d.x1,
            d.y1,
            d.x2,
            d.y2,
        )

    if not extra:
        return defects
    return list(defects) + extra
