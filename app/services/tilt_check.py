"""Оценка отклонения ориентации компонента по кропу bbox (minAreaRect по контуру)."""

from __future__ import annotations

import math

import cv2
import numpy as np


def max_axis_tilt_degrees(rgb_crop: np.ndarray) -> float | None:
    """Минимальное расстояние до горизонтали/вертикали в градусах для доминантного прямоугольника.

    Возвращает None, если контур не найден (слишком мало данных).
    """
    if rgb_crop is None or rgb_crop.size < 400:
        return None
    gray = cv2.cvtColor(rgb_crop, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bw = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) < 20:
        return None

    rect = cv2.minAreaRect(cnt)
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
