"""Предварительная обработка изображений (ТЗ Ф4, доп. к п. 3 пайплайна).

Реализует:
* загрузку изображения из файла или байтового потока;
* приведение к цветовому пространству RGB;
* базовую нормализацию освещённости в :func:`preprocess_image` (CLAHE по Y в YCrCb);
* проверку минимального разрешения (ТЗ 4.1.3 — 640×640 пикселей);
* опциональный конвейер перед детекцией: устранение дисторсии (по JSON-калибровке),
  шумоподавление с сохранением границ, локальное контрастирование.

**Шум перед YOLO:** в конвейере детекции намеренно *нет* изотропного размытия по закону Гаусса
(OpenCV ``Gaussian*`` + ``Blur`` как единый вызов не применяется) — такое сглаживание размывает блики
WS2811-RGB на припое и лишает модель градиентов для оценки мениска. Используются только билатеральный
и медианный фильтры (:func:`apply_bilateral_rgb`, :func:`apply_median_rgb`).

**Освещение платы:** CLAHE в YCrCb или top-hat по L в Lab (:func:`apply_clahe_ycrcb`,
:func:`apply_tophat_lab_luminance`) — см. ``docs/TZ_section3_preprocessing.md``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from ..config import settings


logger = logging.getLogger(__name__)

MIN_RESOLUTION = 640  # ТЗ п. 4.1.3

_calib_pair: tuple[np.ndarray, np.ndarray] | None = None
_calib_resolved_path: str | None = None


class ImageValidationError(ValueError):
    """Изображение не соответствует требованиям ТЗ."""


def load_image(data: bytes | Path) -> np.ndarray:
    """Загружает изображение и возвращает массив HxWx3 в порядке RGB.

    :raises ImageValidationError: если файл не распознан как изображение или
        не соответствует требованиям к разрешению.
    """
    if isinstance(data, (str, Path)):
        img_bgr = cv2.imread(str(data), cv2.IMREAD_COLOR)
    else:
        arr = np.frombuffer(data, dtype=np.uint8)
        img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img_bgr is None:
        raise ImageValidationError(
            "Не удалось декодировать изображение. Поддерживаются форматы JPEG и PNG."
        )

    h, w = img_bgr.shape[:2]
    if min(h, w) < MIN_RESOLUTION:
        raise ImageValidationError(
            f"Минимальное разрешение изображения — {MIN_RESOLUTION}×{MIN_RESOLUTION} пикселей. "
            f"Получено {w}×{h}."
        )

    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)


def preprocess_image(rgb: np.ndarray) -> np.ndarray:
    """Нормализация освещённости через CLAHE в пространстве YCrCb.

    Возвращает изображение того же размера и типа uint8 в формате RGB.
    """
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)

    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    y, cr, cb = cv2.split(ycrcb)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    y_eq = clahe.apply(y)
    merged = cv2.merge((y_eq, cr, cb))
    return cv2.cvtColor(merged, cv2.COLOR_YCrCb2RGB)


def load_calibration_matrices(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Читает camera_matrix и dist_coeffs из JSON (формат выхода cv2.calibrateCamera)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    K = np.asarray(raw["camera_matrix"], dtype=np.float64)
    dist = np.asarray(raw["dist_coeffs"], dtype=np.float64).reshape(-1)
    if K.shape != (3, 3):
        raise ValueError("camera_matrix должен быть массивом 3×3")
    if dist.size < 1:
        raise ValueError("dist_coeffs не может быть пустым")
    return K, dist


def _calibration_or_none() -> tuple[np.ndarray, np.ndarray] | None:
    global _calib_pair, _calib_resolved_path
    cfg = settings.detection_calibration_json
    if cfg is None:
        return None
    resolved = str(Path(cfg).expanduser().resolve())
    if _calib_pair is not None and _calib_resolved_path == resolved:
        return _calib_pair
    path = Path(cfg).expanduser()
    if not path.is_file():
        logger.warning("Файл калибровки камеры не найден: %s", path)
        _calib_pair, _calib_resolved_path = None, resolved
        return None
    try:
        _calib_pair = load_calibration_matrices(path)
        _calib_resolved_path = resolved
        logger.info("Загружена калибровка камеры: %s", path.name)
        return _calib_pair
    except Exception as exc:  # noqa: BLE001
        logger.error("Не удалось разобрать калибровку %s: %s", path, exc)
        _calib_pair, _calib_resolved_path = None, resolved
        return None


def _odd_kernel(k: int, minimum: int = 3, maximum: int = 127) -> int:
    k = int(k)
    k = max(minimum, min(maximum, k))
    if k % 2 == 0:
        k += 1
    return k


def apply_lens_undistort_rgb(rgb: np.ndarray, k: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """cv2.undistort для RGB (геометрия → те же размеры кадра)."""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bgr_u = cv2.undistort(bgr, k, dist, None, None)
    return cv2.cvtColor(bgr_u, cv2.COLOR_BGR2RGB)


def apply_bilateral_rgb(rgb: np.ndarray, d: int, sigma_color: float, sigma_space: float) -> np.ndarray:
    d = _odd_kernel(d, minimum=3, maximum=15)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bgr_f = cv2.bilateralFilter(bgr, d, float(sigma_color), float(sigma_space))
    return cv2.cvtColor(bgr_f, cv2.COLOR_BGR2RGB)


def apply_median_rgb(rgb: np.ndarray, ksize: int) -> np.ndarray:
    k = _odd_kernel(ksize, minimum=3, maximum=9)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bgr_m = cv2.medianBlur(bgr, k)
    return cv2.cvtColor(bgr_m, cv2.COLOR_BGR2RGB)


def apply_clahe_ycrcb(
    rgb: np.ndarray,
    *,
    clip_limit: float,
    tile_grid: int,
) -> np.ndarray:
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    tg = max(2, min(32, int(tile_grid)))
    cl = float(clip_limit)
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    y, cr, cb = cv2.split(ycrcb)
    clahe = cv2.createCLAHE(clipLimit=cl, tileGridSize=(tg, tg))
    y_eq = clahe.apply(y)
    merged = cv2.merge((y_eq, cr, cb))
    return cv2.cvtColor(merged, cv2.COLOR_YCrCb2RGB)


def apply_tophat_lab_luminance(rgb: np.ndarray, kernel_size: int) -> np.ndarray:
    """Top-hat по яркости L в Lab: подавление медленных изменений фона / виньетирования."""
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    k = _odd_kernel(kernel_size, minimum=9, maximum=127)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    bg = cv2.morphologyEx(l_ch, cv2.MORPH_OPEN, kernel)
    l_f = l_ch.astype(np.float32)
    bg_f = bg.astype(np.float32)
    mean_l = float(np.mean(l_f))
    l_out = np.clip(l_f - bg_f + mean_l, 0, 255).astype(np.uint8)
    merged = cv2.merge((l_out, a_ch, b_ch))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)


def apply_detection_preprocess(rgb: np.ndarray) -> np.ndarray:
    """Единая точка входа перед ``Detector.predict``.

    На выходе — RGB uint8 того же разрешения, что и вход (YOLO по-прежнему видит RGB).

    Важно: при включённом устранении дисторсии координаты боксов считаются в
    выпрямленном изображении; клиент live-потока должен либо сам выпрямлять кадр,
    либо отключить этот этап для наложения рамок «поверх сырого» MJPEG.
    """
    if not getattr(settings, "detection_preprocess_enabled", False):
        return rgb
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ImageValidationError("Ожидается цветное RGB-изображение H×W×3.")

    out = np.ascontiguousarray(rgb)
    if out.dtype != np.uint8:
        out = np.clip(out, 0, 255).astype(np.uint8)

    if settings.preprocess_lens_undistort:
        cal = _calibration_or_none()
        if cal is not None:
            k_mat, dist = cal
            out = apply_lens_undistort_rgb(out, k_mat, dist)

    nf = settings.preprocess_noise_filter
    if nf == "bilateral":
        out = apply_bilateral_rgb(
            out,
            settings.preprocess_bilateral_d,
            settings.preprocess_bilateral_sigma_color,
            settings.preprocess_bilateral_sigma_space,
        )
    elif nf == "median":
        out = apply_median_rgb(out, settings.preprocess_median_ksize)
    elif nf == "none":
        pass
    else:  # pragma: no cover — Literal в Settings
        logger.warning("Неизвестный preprocess_noise_filter=%r, шум не фильтруется.", nf)

    illum = settings.preprocess_illumination
    if illum == "clahe":
        out = apply_clahe_ycrcb(
            out,
            clip_limit=settings.preprocess_clahe_clip_limit,
            tile_grid=settings.preprocess_clahe_tile_grid,
        )
    elif illum == "tophat_lab":
        out = apply_tophat_lab_luminance(out, settings.preprocess_tophat_kernel_size)
    elif illum == "none":
        pass
    else:  # pragma: no cover — Literal в Settings
        logger.warning("Неизвестный preprocess_illumination=%r, этап пропущен.", illum)

    return out


def save_image(rgb: np.ndarray, path: Path) -> None:
    """Сохраняет изображение в формате PNG (без потерь)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(path), bgr, [cv2.IMWRITE_PNG_COMPRESSION, 3])
