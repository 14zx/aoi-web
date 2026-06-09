"""Оценка отклонения ориентации компонента по кропу bbox (minAreaRect по контуру)."""

from __future__ import annotations

import math

import cv2
import numpy as np

# Компоненты, близкие к квадрату, не имеют выраженной длинной оси — для них
# угол minAreaRect нестабилен (шумит на ±45°), поэтому наклон не оцениваем.
_MIN_ASPECT_RATIO = 1.25


def max_axis_tilt_degrees(rgb_crop: np.ndarray) -> float | None:
    """Минимальное расстояние до горизонтали/вертикали в градусах для доминантного прямоугольника.

    Возвращает ``None``, если оценить наклон надёжно нельзя:
    * контур не найден / слишком мал;
    * силуэт почти квадратный (нет выраженной длинной оси);
    * найденный контур повторяет рамку кропа (фон, а не компонент).
    """
    if rgb_crop is None or rgb_crop.size < 400:
        return None
    h, w = rgb_crop.shape[:2]
    if h < 8 or w < 8:
        return None

    gray = cv2.cvtColor(rgb_crop, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bw = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    # Otsu может инвертировать силуэт (светлый компонент на тёмном фоне или
    # наоборот). Берём вариант, где «объект» (белые пиксели) занимает меньшую
    # часть кадра — это почти всегда компонент, а не фон.
    if int(np.count_nonzero(bw)) > bw.size // 2:
        bw = cv2.bitwise_not(bw)
    # Закрываем мелкие разрывы, чтобы контур компонента был цельным.
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    crop_area = float(h * w)
    if area < max(20.0, crop_area * 0.02):
        return None
    # Контур, занимающий почти весь кроп, — это рамка/фон, а не компонент.
    if area > crop_area * 0.95:
        return None

    rect = cv2.minAreaRect(cnt)
    (_, _), (rw, rh), _ = rect
    if min(rw, rh) < 1.0:
        return None
    aspect = max(rw, rh) / max(1.0, min(rw, rh))
    if aspect < _MIN_ASPECT_RATIO:
        # Почти квадрат — длинная ось не определена, наклон не меряем.
        return None

    box = cv2.boxPoints(rect)
    box = np.asarray(box, dtype=np.float32)
    # Длины рёбер и угол самого длинного к горизонту
    best_len = 0.0
    best_theta = 0.0
    for i in range(4):
        p, q = box[i], box[(i + 1) % 4]
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dy)
        if ln > best_len:
            best_len = ln
            best_theta = math.degrees(math.atan2(dy, dx))

    # Угол относительно 0° или 90°: брать минимальное отклонение от оси
    t = abs(best_theta) % 180.0
    deviation = min(t, abs(90.0 - t))
    return float(deviation)
