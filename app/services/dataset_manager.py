"""Управление датасетами (весами модели детекции).

Файлы весов хранятся в ``BASE_DIR / 'models' / 'datasets' / <id> /``.
Активный датасет ровно один; его путь записан в БД (``is_active=True``).
При активации — детектор перезагружает модель.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BASE_DIR, settings
from ..models import Dataset
from .detector import get_detector


logger = logging.getLogger(__name__)

DATASETS_DIR = Path(BASE_DIR) / "models" / "datasets"
DATASETS_DIR.mkdir(parents=True, exist_ok=True)


def _dataset_dir(dataset_id: int) -> Path:
    return DATASETS_DIR / str(dataset_id)


def absolute_path(dataset: Dataset) -> Path:
    p = Path(dataset.file_path)
    if not p.is_absolute():
        p = Path(BASE_DIR) / p
    return p


def store_weights_file(
    dataset_id: int,
    src: BinaryIO,
    original_filename: str,
    max_bytes: int | None = None,
) -> tuple[Path, int]:
    """Сохраняет входной файл в каталог датасета.

    :return: кортеж ``(полный_путь, размер_в_байтах)``.
    """
    dest_dir = _dataset_dir(dataset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(original_filename).suffix.lower() or ".pt"
    dest = dest_dir / f"weights{ext}"

    total = 0
    with dest.open("wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise ValueError("Файл превышает допустимый размер")
            out.write(chunk)
    return dest, total


def remove_files(dataset: Dataset) -> None:
    try:
        dest_dir = _dataset_dir(dataset.id)
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
    except Exception as exc:  # pragma: no cover
        logger.warning("Не удалось удалить файлы датасета %s: %s", dataset.id, exc)


def activate(db: Session, dataset: Dataset) -> str:
    """Делает датасет активным и перезагружает детектор.

    :return: backend, с которым поднялся детектор (``yolov8``/``fallback``).
    """
    # Снимаем активность со всех остальных.
    others = db.execute(select(Dataset).where(Dataset.id != dataset.id)).scalars().all()
    for other in others:
        other.is_active = False
    dataset.is_active = True
    db.flush()

    path = absolute_path(dataset)
    backend = get_detector().reload(path, weights_label=dataset.name)
    logger.info(
        'Активирован датасет «%s» (id=%s), backend=%s',
        dataset.name,
        dataset.id,
        backend,
    )
    return backend


def reload_detector_to_preferred_weights(db: Session) -> None:
    """Порядок: ``MODEL_WEIGHTS_PATH`` если файл есть, иначе активный датасет, иначе fallback."""
    cfg = Path(settings.model_weights_path)
    if cfg.exists():
        get_detector().reload(cfg)
        return
    ds = get_active(db)
    if ds is not None:
        path = absolute_path(ds)
        if path.exists():
            get_detector().reload(path, weights_label=ds.name)
            return
    get_detector().reload(None)


def deactivate_all(db: Session) -> None:
    """Сбрасывает активный датасет (детектор переключается на веса из конфига)."""
    for ds in db.execute(select(Dataset).where(Dataset.is_active.is_(True))).scalars().all():
        ds.is_active = False
    db.flush()
    reload_detector_to_preferred_weights(db)


def get_active(db: Session) -> Dataset | None:
    return db.execute(select(Dataset).where(Dataset.is_active.is_(True))).scalar_one_or_none()


def ensure_detector_synced(db: Session) -> None:
    """Вызывается на старте: веса из конфига важнее активного датасета в БД."""
    reload_detector_to_preferred_weights(db)
