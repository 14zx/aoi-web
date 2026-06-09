"""Поиск перемычек припоя (solder bridge) между соседними компонентами/выводами.

Обученной головы под этот класс в основной модели нет, поэтому дефект ищется
эвристикой по кадру: припой — яркий и малонасыщенный (металлический блик). Если
между двумя близко стоящими компонентами зазор «залит» таким припоем и образует
сплошной мостик — добавляется дефект ``solder_bridge``.

Работает аналогично ``post_detection.apply_component_tilt_rules`` и
``golden_region_check`` — как постобработка над списком детекций YOLO.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from .class_semantics import is_component_class, load_mappings
from .detector import NAME_BY_CODE, DetectedDefect

logger = logging.getLogger(__name__)

CLASS_SOLDER_BRIDGE = "solder_bridge"

# Минимальная длина общей грани (в px), чтобы считать компоненты соседними.
_MIN_SHARED_EDGE_PX = 4
# Минимальная доля перекрытия по общей грани относительно меньшего компонента.
_MIN_OVERLAP_FRACTION = 0.3


def _solder_mask(rgb: np.ndarray) -> np.ndarray:
    """Бинарная маска «припойных» пикселей: яркие и малонасыщенные (HSV)."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    min_v = int(settings.solder_bridge_min_brightness)
    max_s = int(settings.solder_bridge_max_saturation)
    mask = ((v >= min_v) & (s <= max_s)).astype(np.uint8)
    # Убираем одиночный шум, склеиваем мелкие разрывы припоя.
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return mask


def _overlap(a_lo: int, a_hi: int, b_lo: int, b_hi: int) -> tuple[int, int]:
    """Пересечение двух отрезков [lo, hi); (0, 0) если не пересекаются."""
    lo = max(a_lo, b_lo)
    hi = min(a_hi, b_hi)
    return (lo, hi) if hi > lo else (0, 0)


def _bridge_fill_horizontal(
    mask: np.ndarray, y_lo: int, y_hi: int, x_lo: int, x_hi: int
) -> float:
    """Доля строк зазора, полностью «залитых» припоем (горизонтальный мостик)."""
    strip = mask[y_lo:y_hi, x_lo:x_hi]
    if strip.size == 0 or strip.shape[1] == 0:
        return 0.0
    rows_full = np.all(strip > 0, axis=1)
    return float(np.count_nonzero(rows_full)) / float(strip.shape[0])


def _bridge_fill_vertical(
    mask: np.ndarray, y_lo: int, y_hi: int, x_lo: int, x_hi: int
) -> float:
    """Доля столбцов зазора, полностью «залитых» припоем (вертикальный мостик)."""
    strip = mask[y_lo:y_hi, x_lo:x_hi]
    if strip.size == 0 or strip.shape[0] == 0:
        return 0.0
    cols_full = np.all(strip > 0, axis=0)
    return float(np.count_nonzero(cols_full)) / float(strip.shape[1])


def _make_bridge_defect(
    x1: int, y1: int, x2: int, y2: int, fill: float
) -> DetectedDefect:
    return DetectedDefect(
        class_code=CLASS_SOLDER_BRIDGE,
        class_name=NAME_BY_CODE.get(CLASS_SOLDER_BRIDGE, "Перемычка припоя"),
        confidence=float(min(0.95, 0.5 + fill * 0.45)),
        x1=int(x1),
        y1=int(y1),
        x2=int(x2),
        y2=int(y2),
    )


def find_solder_bridges(
    rgb: np.ndarray,
    defects: list[DetectedDefect],
    db: Session,
) -> list[DetectedDefect]:
    """Добавляет дефекты ``solder_bridge`` между соседними компонентами с мостиком припоя."""
    if not bool(getattr(settings, "solder_bridge_check_enabled", True)):
        return defects
    if rgb is None or rgb.size == 0:
        return defects

    mappings = load_mappings(db)
    components = [d for d in defects if is_component_class(d.class_code, mappings)]
    if len(components) < 2:
        return defects

    h, w = rgb.shape[:2]
    max_gap = int(settings.solder_bridge_max_gap_px)
    min_fill = float(settings.solder_bridge_min_fill)
    mask = _solder_mask(rgb)

    extra: list[DetectedDefect] = []
    seen: set[tuple[int, int, int, int]] = set()

    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            a, b = components[i], components[j]

            # --- Горизонтальное соседство (зазор по X) ---
            left, right = (a, b) if a.x2 <= b.x2 else (b, a)
            gap_x = right.x1 - left.x2
            oy_lo, oy_hi = _overlap(a.y1, a.y2, b.y1, b.y2)
            shared_v = oy_hi - oy_lo
            min_h = min(a.y2 - a.y1, b.y2 - b.y1)
            if (
                0 < gap_x <= max_gap
                and shared_v >= _MIN_SHARED_EDGE_PX
                and min_h > 0
                and shared_v >= _MIN_OVERLAP_FRACTION * min_h
            ):
                x_lo = max(0, left.x2)
                x_hi = min(w, right.x1)
                y_lo = max(0, oy_lo)
                y_hi = min(h, oy_hi)
                fill = _bridge_fill_horizontal(mask, y_lo, y_hi, x_lo, x_hi)
                if fill >= min_fill:
                    key = (x_lo, y_lo, x_hi, y_hi)
                    if key not in seen:
                        seen.add(key)
                        extra.append(_make_bridge_defect(x_lo, y_lo, x_hi, y_hi, fill))
                        logger.debug(
                            "solder_bridge (гориз.) между %s и %s, fill=%.2f gap=%dpx",
                            a.class_code, b.class_code, fill, gap_x,
                        )
                continue

            # --- Вертикальное соседство (зазор по Y) ---
            top, bottom = (a, b) if a.y2 <= b.y2 else (b, a)
            gap_y = bottom.y1 - top.y2
            ox_lo, ox_hi = _overlap(a.x1, a.x2, b.x1, b.x2)
            shared_h = ox_hi - ox_lo
            min_w = min(a.x2 - a.x1, b.x2 - b.x1)
            if (
                0 < gap_y <= max_gap
                and shared_h >= _MIN_SHARED_EDGE_PX
                and min_w > 0
                and shared_h >= _MIN_OVERLAP_FRACTION * min_w
            ):
                x_lo = max(0, ox_lo)
                x_hi = min(w, ox_hi)
                y_lo = max(0, top.y2)
                y_hi = min(h, bottom.y1)
                fill = _bridge_fill_vertical(mask, y_lo, y_hi, x_lo, x_hi)
                if fill >= min_fill:
                    key = (x_lo, y_lo, x_hi, y_hi)
                    if key not in seen:
                        seen.add(key)
                        extra.append(_make_bridge_defect(x_lo, y_lo, x_hi, y_hi, fill))
                        logger.debug(
                            "solder_bridge (вертик.) между %s и %s, fill=%.2f gap=%dpx",
                            a.class_code, b.class_code, fill, gap_y,
                        )

    if not extra:
        return defects
    logger.info("Найдено перемычек припоя: %d", len(extra))
    return list(defects) + extra
