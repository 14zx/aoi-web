"""Выравнивание кадра относительно эталона (ТЗ п. 3 — Image Alignment, ECC)."""

from __future__ import annotations

import logging
from typing import Literal

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MotionMode = Literal["affine", "euclidean"]


def _to_gray_u8(rgb: np.ndarray) -> np.ndarray:
    if rgb.ndim != 2:
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return rgb.astype(np.uint8, copy=False)


def align_rgb_ecc(
    moving_rgb: np.ndarray,
    reference_rgb: np.ndarray,
    *,
    max_iters: int = 80,
    eps: float = 1e-6,
    motion: MotionMode = "affine",
    gaussian_filt_size: int = 1,
) -> tuple[np.ndarray, float]:
    """Выравнивает ``moving_rgb`` к ``reference_rgb`` по яркости (ECC).

    Возвращает пару ``(warped_rgb, mae)`` — MAE по grayscale после выравнивания.
    При ошибке ECC возвращает исходное ``moving_rgb`` (после ресайза под эталон) и ``mae=inf``.
    """
    if moving_rgb.ndim != 3 or reference_rgb.ndim != 3:
        raise ValueError("Ожидаются изображения H×W×3 RGB.")
    ref = _to_gray_u8(reference_rgb)
    h, w = ref.shape[:2]
    mov = moving_rgb.astype(np.uint8, copy=False)
    if mov.shape[:2] != (h, w):
        mov = cv2.resize(mov, (w, h), interpolation=cv2.INTER_AREA)
    mov_g = _to_gray_u8(mov)

    motion_type = cv2.MOTION_AFFINE if motion == "affine" else cv2.MOTION_EUCLIDEAN
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, int(max_iters), float(eps))
    try:
        cv2.findTransformECC(
            ref,
            mov_g,
            warp,
            motionType=motion_type,
            criteria=criteria,
            inputMask=None,
            gaussFiltSize=int(gaussian_filt_size),
        )
    except cv2.error as exc:  # noqa: BLE001
        logger.warning("ECC alignment failed: %s", exc)
        mae = float(np.mean(np.abs(mov_g.astype(np.float32) - ref.astype(np.float32))))
        return mov, mae

    warped = cv2.warpAffine(
        mov,
        warp,
        (w, h),
        flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REFLECT,
    )
    wg = _to_gray_u8(warped)
    mae = float(np.mean(np.abs(wg.astype(np.float32) - ref.astype(np.float32))))
    return warped, mae
