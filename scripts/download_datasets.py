"""Скачивание публичных весов PCB-детекторов и их регистрация в системе.

Запуск:

    python -m scripts.download_datasets              # скачать все + активировать первый рабочий
    python -m scripts.download_datasets --only pku   # скачать только конкретный пресет
    python -m scripts.download_datasets --list       # показать доступные пресеты
    python -m scripts.download_datasets --no-activate

После работы скрипта вы увидите записи в таблице ``datasets`` и файлы в
``models/datasets/<id>/weights.pt``. Активный датасет сразу подхватывается
детектором (hot-reload). В админ-панели (вкладка «Датасеты») их можно
переключать, удалять и загружать новые.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Путь к корню проекта добавляем в sys.path, чтобы можно было импортировать app.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.config import BASE_DIR  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Dataset  # noqa: E402  (импорт необходим для регистрации модели)
from app.services import dataset_manager  # noqa: E402


logger = logging.getLogger("download_datasets")


@dataclass(frozen=True)
class DatasetPreset:
    """Описание готового к скачиванию набора весов."""

    key: str
    name: str
    description: str
    url: str
    original_filename: str
    expected_min_bytes: int  # защита от «битой» закачки (0 байт / HTML-заглушка)


# ВАЖНО: все пресеты — уже ОБУЧЕННЫЕ веса YOLOv8 для дефектов PCB.
# Детектор умеет нормализовать имена классов (см. services/detector.py).
PRESETS: dict[str, DatasetPreset] = {
    "pku": DatasetPreset(
        key="pku",
        name="YOLOv8n PCB (PKU-Market / akhatova, v1)",
        description=(
            "Публичные веса YOLOv8n, обученные на PKU-Market-PCB (датасет akhatova, Kaggle). "
            "Классы: missing_hole, mouse_bite, open_circuit, short, spur, spurious_copper. "
            "Автор: ampragatish. Лицензия: Apache-2.0. "
            "Подходит как baseline для демонстрации и отладки. "
            "Источник: https://huggingface.co/ampragatish/yolov8n-pcb-defects-detection"
        ),
        url="https://huggingface.co/ampragatish/yolov8n-pcb-defects-detection/resolve/main/best.pt",
        original_filename="yolov8n-pcb-pku.pt",
        expected_min_bytes=1_000_000,  # файл реально ~6 МБ
    ),
    "pcb_seg_s": DatasetPreset(
        key="pcb_seg_s",
        name="YOLOv8s PCB segmentation (keremberke)",
        description=(
            "YOLOv8s-seg, обучена на PCB дефектах (keremberke). "
            "ВНИМАНИЕ: это модель сегментации с другим набором классов "
            "(Dry_joint, Incorrect_installation, PCB_damage, Short_circuit). "
            "Наш детектор использует её в режиме детекции по bbox; "
            "имена нестандартных классов будут показаны как есть. "
            "Держим её как альтернативу. "
            "Источник: https://huggingface.co/keremberke/yolov8s-pcb-defect-segmentation"
        ),
        url="https://huggingface.co/keremberke/yolov8s-pcb-defect-segmentation/resolve/main/best.pt",
        original_filename="yolov8s-pcb-seg-keremberke.pt",
        expected_min_bytes=10_000_000,  # ~24 МБ
    ),
}


def _download(url: str, dest: Path) -> int:
    """Скачивает файл по ``url`` в ``dest``, возвращает размер в байтах."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AOI-Web/1.0 (+https://example.local)",
            "Accept": "*/*",
        },
    )
    total = 0
    sha1 = hashlib.sha1()
    last_logged = 0
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            sha1.update(chunk)
            total += len(chunk)
            if total - last_logged >= 4 * 1024 * 1024:
                logger.info("  ... скачано %.1f МБ", total / 1024 / 1024)
                last_logged = total
    tmp.replace(dest)
    logger.info("  готово: %s (%.1f МБ, sha1=%s...)", dest.name, total / 1024 / 1024, sha1.hexdigest()[:10])
    return total


def _ensure_dataset(db, preset: DatasetPreset) -> tuple[Dataset, bool]:
    """Возвращает ``(dataset, created)``. Если запись уже была — её же.

    Файл при этом всегда пере-скачивается, чтобы гарантировать целостность.
    """
    existing = db.execute(
        select(Dataset).where(Dataset.name == preset.name)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    ds = Dataset(
        name=preset.name,
        description=preset.description,
        file_path="",  # заполним после скачивания
        file_size=0,
        original_filename=preset.original_filename,
        is_active=False,
        uploaded_by_id=None,  # авто-импорт, нет автора
    )
    db.add(ds)
    db.flush()  # чтобы получить id
    return ds, True


def _download_into_dataset(preset: DatasetPreset, ds: Dataset) -> Path:
    """Скачивает файл весов в каталог датасета (``models/datasets/<id>/weights.pt``)."""
    target_dir = Path(BASE_DIR) / "models" / "datasets" / str(ds.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    # Расширение возьмём из URL (у всех наших пресетов — .pt).
    ext = Path(preset.url.split("?")[0]).suffix or ".pt"
    target = target_dir / f"weights{ext}"

    logger.info("Скачиваю %s", preset.url)
    size = _download(preset.url, target)

    if size < preset.expected_min_bytes:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"Скачанный файл слишком мал ({size} байт). "
            f"Возможно, сервер вернул HTML. Попробуйте ещё раз."
        )

    return target


def _sanity_check(weights: Path) -> tuple[bool, str]:
    """Проверяет, что веса грузятся Ultralytics YOLO."""
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as exc:  # pragma: no cover
        return False, f"ultralytics недоступен: {exc}"
    try:
        model = YOLO(str(weights))
        names = getattr(model, "names", None)
        return True, f"ok, классы: {names}"
    except Exception as exc:
        return False, f"ошибка загрузки YOLO: {exc}"


def run(presets: Iterable[str], *, activate_first: bool) -> int:
    # На случай «чистой» БД без Alembic — гарантируем наличие таблиц.
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    activated_key: str | None = None
    try:
        for key in presets:
            preset = PRESETS.get(key)
            if preset is None:
                logger.error("Неизвестный пресет: %s", key)
                continue

            logger.info("=== %s ===", preset.name)
            ds, created = _ensure_dataset(db, preset)
            try:
                weights_path = _download_into_dataset(preset, ds)
            except Exception as exc:
                logger.error("Не удалось скачать %s: %s", preset.key, exc)
                if created:
                    # отменяем пустую запись
                    db.delete(ds)
                    db.commit()
                continue

            ok, info = _sanity_check(weights_path)
            logger.info("Проверка весов: %s (%s)", "OK" if ok else "FAIL", info)
            if not ok:
                logger.warning(
                    "Оставляем запись %r, но активировать НЕ будем.",
                    preset.name,
                )

            try:
                rel = weights_path.relative_to(BASE_DIR)
            except ValueError:
                rel = weights_path
            ds.file_path = str(rel).replace("\\", "/")
            ds.file_size = weights_path.stat().st_size
            db.commit()
            db.refresh(ds)

            if ok and activate_first and activated_key is None:
                backend = dataset_manager.activate(db, ds)
                db.commit()
                activated_key = preset.key
                logger.info(
                    "Датасет %r активирован как основной, детектор backend=%s",
                    preset.name,
                    backend,
                )

        logger.info("Сводка:")
        for ds in db.execute(select(Dataset).order_by(Dataset.id)).scalars():
            logger.info(
                "  #%d  %-50s  %7.1f KB  active=%s  file=%s",
                ds.id,
                ds.name,
                ds.file_size / 1024,
                ds.is_active,
                ds.file_path,
            )
        return 0
    finally:
        db.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Скачивание публичных PCB YOLOv8 весов.")
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Скачать только указанный пресет (можно повторять). По умолчанию — все.",
    )
    parser.add_argument("--list", action="store_true", help="Показать список пресетов и выйти.")
    parser.add_argument(
        "--no-activate",
        action="store_true",
        help="Не делать ни один датасет активным.",
    )
    args = parser.parse_args()

    if args.list:
        for key, p in PRESETS.items():
            print(f"* {key}: {p.name}\n    {p.description}\n    {p.url}\n")
        return 0

    keys = args.only or list(PRESETS.keys())
    return run(keys, activate_first=not args.no_activate)


if __name__ == "__main__":
    raise SystemExit(main())
