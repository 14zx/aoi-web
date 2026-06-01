"""Выравнивание кадра инспекции по эталону Golden Board (ТЗ п. 3, п. 5).

Контракт JSON эталона (в ``GoldenBoardProfile.payload_json``):

* ``reference_image_rel`` — путь к файлу эталона **относительно** ``storage_dir``
  (JPEG/PNG, то же минимальное разрешение, что и для инспекций);
* либо объект ``reference`` с полем ``image_rel`` или ``image_storage_rel``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import Settings
from .alignment import align_rgb_ecc
from .preprocessing import ImageValidationError, load_image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoldenAlignResult:
    """Результат попытки выравнивания по эталону."""

    rgb: np.ndarray
    applied: bool
    mae_before: float | None
    mae_after: float | None
    detail: str | None = None
    compare_ready: bool = False


def _parse_payload_json(raw: str) -> dict | list:
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"payload_json не JSON: {exc}") from exc
    return data


def extract_reference_image_rel(payload: dict | list) -> str | None:
    """Возвращает относительный путь к файлу эталона или ``None``."""
    if isinstance(payload, dict):
        rel = payload.get("reference_image_rel")
        if isinstance(rel, str) and rel.strip():
            return rel.strip().replace("\\", "/")
        ref = payload.get("reference")
        if isinstance(ref, dict):
            for key in ("image_rel", "image_storage_rel", "image_path"):
                v = ref.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip().replace("\\", "/")
    return None


def resolve_reference_path(storage_dir: Path, rel: str) -> Path:
    """Проверяет путь и возвращает абсолютный путь внутри ``storage_dir``."""
    r = rel.strip().replace("\\", "/")
    if not r or r.startswith("/") or Path(r).is_absolute() or ".." in Path(r).parts:
        raise ValueError("Недопустимый reference_image_rel")
    base = storage_dir.resolve()
    target = (storage_dir / r).resolve()
    if base != target and base not in target.parents:
        raise ValueError("reference_image_rel выходит за пределы storage_dir")
    return target


def _mae_gray_same_size(a_rgb: np.ndarray, b_rgb: np.ndarray) -> float:
    """MAE по grayscale для кадров одинакового размера."""
    ag = np.mean(a_rgb, axis=2).astype(np.float32)
    bg = np.mean(b_rgb, axis=2).astype(np.float32)
    return float(np.mean(np.abs(ag - bg)))


def _should_use_aligned(mae_before: float, mae_after: float) -> bool:
    """Та же эвристика, что в ``/api/pipeline/alignment/demo``."""
    return mae_after < mae_before * 0.98 or mae_after < 2.0


def align_rgb_with_golden_profile(
    rgb: np.ndarray,
    *,
    payload_json: str,
    settings: Settings,
) -> GoldenAlignResult:
    """Пытается выровнять ``rgb`` к эталону из профиля. При ошибке — исходный кадр."""
    payload = _parse_payload_json(payload_json)
    rel = extract_reference_image_rel(payload)
    if not rel:
        return GoldenAlignResult(
            rgb=rgb,
            applied=False,
            mae_before=None,
            mae_after=None,
            detail="В эталоне не задан reference_image_rel (или reference.image_rel)",
            compare_ready=False,
        )
    try:
        ref_path = resolve_reference_path(settings.storage_dir, rel)
    except ValueError as exc:
        return GoldenAlignResult(
            rgb=rgb,
            applied=False,
            mae_before=None,
            mae_after=None,
            detail=str(exc),
            compare_ready=False,
        )
    if not ref_path.is_file():
        return GoldenAlignResult(
            rgb=rgb,
            applied=False,
            mae_before=None,
            mae_after=None,
            detail=f"Файл эталона не найден: {rel}",
            compare_ready=False,
        )
    try:
        ref_rgb = load_image(ref_path)
    except ImageValidationError as exc:
        return GoldenAlignResult(
            rgb=rgb,
            applied=False,
            mae_before=None,
            mae_after=None,
            detail=f"Эталон: {exc}",
            compare_ready=False,
        )

    h, w = ref_rgb.shape[:2]
    mov = rgb.astype(np.uint8, copy=False)
    if mov.shape[:2] != (h, w):
        mov = cv2.resize(mov, (w, h), interpolation=cv2.INTER_AREA)
    mae_before = _mae_gray_same_size(mov, ref_rgb)

    warped, mae_after = align_rgb_ecc(
        rgb,
        ref_rgb,
        max_iters=settings.alignment_ecc_max_iters,
        motion=settings.alignment_ecc_motion,
    )
    if not _should_use_aligned(mae_before, mae_after):
        compare_without_ecc = bool(getattr(settings, "golden_compare_without_ecc", True))
        if compare_without_ecc:
            return GoldenAlignResult(
                rgb=mov,
                applied=False,
                mae_before=mae_before,
                mae_after=mae_after,
                detail="ECC не улучшил кадр; сверка с эталоном по масштабу (без warp)",
                compare_ready=True,
            )
        return GoldenAlignResult(
            rgb=rgb,
            applied=False,
            mae_before=mae_before,
            mae_after=mae_after,
            detail="ECC не улучшил выравнивание; детекция по исходному кадру",
            compare_ready=False,
        )
    return GoldenAlignResult(
        rgb=warped,
        applied=True,
        mae_before=mae_before,
        mae_after=mae_after,
        detail=None,
        compare_ready=True,
    )
