"""Обучение единой модели детекции АОИ.

Использует:
    * реестр классов ``models/unified_classes.yaml``;
    * собранный датасет ``datasets/unified/data.yaml`` (сделать его
      должны scripts/prepare_datasets.py и scripts/aggregate_reviews.py).

После завершения копирует ``runs/train/<name>/weights/best.pt`` в
``models/aoi_unified.pt`` — его и цепляет детектор. Обе метрики (mAP50
и mAP50-95) выводит в консоль.

Базовая конфигурация подобрана под RTX 3050 (6 ГБ VRAM). Если память кончается,
уменьшите ``--batch`` или возьмите ``--model yolov8n.pt``.

Запуск (пример)::

    python scripts/train_unified.py --epochs 100
    python scripts/train_unified.py --epochs 50 --batch 8 --model yolov8n.pt
    python scripts/train_unified.py --resume                # продолжить прошлый
    python scripts/train_unified.py --from models/aoi_unified.pt  # дообучение

Для дообучения после новых оценок оператора правильная
последовательность команд::

    python scripts/aggregate_reviews.py            # подлить оценки
    python scripts/train_unified.py --from models/aoi_unified.pt --epochs 30
"""

from __future__ import annotations

import argparse
import logging
import platform
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# Убрали sys.path.insert(0, ROOT): иначе локальный каталог datasets/
# (куда prepare_datasets кладёт YOLO-выборку) скроет одноимённый
# pip-пакет, которым пользуется Ultralytics под капотом.

CLASSES_YAML = ROOT / "models" / "unified_classes.yaml"
DATA_YAML = ROOT / "datasets" / "unified" / "data.yaml"
MODELS_DIR = ROOT / "models"
DEFAULT_OUTPUT = MODELS_DIR / "aoi_unified.pt"

logger = logging.getLogger("train_unified")


def _load_image_size() -> int:
    data = yaml.safe_load(CLASSES_YAML.read_text(encoding="utf-8"))
    return int(data.get("image_size", 640))


def _load_default_base_weights() -> str:
    data = yaml.safe_load(CLASSES_YAML.read_text(encoding="utf-8"))
    return str(data.get("base_weights") or "yolov8s.pt")


def _sanity_check_data() -> None:
    if not DATA_YAML.exists():
        raise SystemExit(
            "Не найден "
            f"{DATA_YAML}. Сначала запустите:\n"
            "  python scripts/prepare_datasets.py\n"
            "и/или\n"
            "  python scripts/aggregate_reviews.py"
        )
    meta = yaml.safe_load(DATA_YAML.read_text(encoding="utf-8"))
    names = meta.get("names") or {}
    if not names:
        raise SystemExit("В datasets/unified/data.yaml пустой список классов.")
    # Проверим, есть ли хоть что-то в train/.
    train_dir = DATA_YAML.parent / (meta.get("train") or "images/train")
    if not train_dir.exists() or not any(train_dir.iterdir()):
        raise SystemExit(f"Каталог обучения пуст: {train_dir}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Обучение единой YOLOv8 модели АОИ")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="По умолчанию берётся из unified_classes.yaml",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Стартовые веса (yolov8n.pt / yolov8s.pt / ...). "
             "По умолчанию — из unified_classes.yaml.",
    )
    parser.add_argument(
        "--from",
        dest="finetune_from",
        default=None,
        help="Дообучать указанные .pt (имеет приоритет над --model)",
    )
    parser.add_argument("--project", default="runs/train")
    parser.add_argument("--name", default="aoi-unified")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--device",
        default="0",
        help="CUDA-устройство (0, 0,1) или 'cpu'",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=30,
        help="EarlyStopping (эпох без улучшения mAP)",
    )
    # На Windows multiprocessing.spawn иногда молча кладёт воркеров
    # DataLoader-а сразу после «Plotting labels...», и обучение тихо
    # завершается с кодом 0. Безопасный дефолт — 0 воркеров на Windows.
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Кол-во DataLoader воркеров (0 по умолчанию на Windows, 8 иначе)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Куда скопировать best.pt после обучения",
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Не копировать best.pt в models/aoi_unified.pt",
    )
    # --- Аугментации геометрии (ключевое для распознавания компонентов под углом) ---
    # У текущих весов diploma обучение шло с degrees=0.0 — модель почти не видела
    # повёрнутых компонентов, поэтому под углом детектирует/обводит плохо.
    parser.add_argument(
        "--degrees",
        type=float,
        default=10.0,
        help="Случайный поворот ±град. при обучении. Для платы в произвольной "
             "ориентации ставьте 180. Включает устойчивость к наклону. (Ultralytics default=0)",
    )
    parser.add_argument(
        "--perspective",
        type=float,
        default=0.0005,
        help="Перспективное искажение (0..0.001). Имитация съёмки под наклоном.",
    )
    parser.add_argument(
        "--shear",
        type=float,
        default=2.0,
        help="Сдвиг (shear) ±град.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=0.5,
        help="Случайный масштаб (gain). Помогает при разном удалении камеры.",
    )
    args = parser.parse_args()

    _sanity_check_data()

    try:
        from ultralytics import YOLO  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Пакет 'ultralytics' не установлен. Выполните 'pip install ultralytics'."
        ) from exc

    imgsz = args.imgsz or _load_image_size()
    base_weights = args.finetune_from or args.model or _load_default_base_weights()

    if args.workers is not None:
        _workers_preview = args.workers
    else:
        _workers_preview = 0 if platform.system() == "Windows" else 8

    logger.info("=" * 60)
    logger.info("Старт обучения")
    logger.info("  data.yaml:   %s", DATA_YAML)
    logger.info("  базовые веса:%s", base_weights)
    logger.info(
        "  imgsz=%d  batch=%d  epochs=%d  device=%s  workers=%d",
        imgsz, args.batch, args.epochs, args.device, _workers_preview,
    )
    logger.info("=" * 60)

    model = YOLO(base_weights)

    # На Windows многопроцессные воркеры DataLoader-а часто падают молча
    # (обучение тихо выходит после «Plotting labels...»). Поэтому по
    # умолчанию отключаем их именно там.
    if args.workers is not None:
        workers = args.workers
    else:
        workers = 0 if platform.system() == "Windows" else 8

    train_kwargs = dict(
        data=str(DATA_YAML),
        epochs=args.epochs,
        imgsz=imgsz,
        batch=args.batch,
        project=args.project,
        name=args.name,
        device=args.device,
        patience=args.patience,
        workers=workers,
        exist_ok=True,
        resume=args.resume,
        # Геометрические аугментации — устойчивость к наклону/повороту компонентов.
        degrees=args.degrees,
        perspective=args.perspective,
        shear=args.shear,
        scale=args.scale,
    )
    logger.info(
        "  аугментации: degrees=%.1f perspective=%.4f shear=%.1f scale=%.2f",
        args.degrees, args.perspective, args.shear, args.scale,
    )
    # У YOLO train(..., resume=True) нельзя параллельно с data=... — уберём.
    if args.resume:
        train_kwargs.pop("data", None)

    results = model.train(**train_kwargs)
    logger.info("Обучение завершено.")

    metrics = model.val()
    try:
        logger.info(
            "Валидация: mAP50=%.3f  mAP50-95=%.3f",
            float(metrics.box.map50),
            float(metrics.box.map),
        )
    except Exception:  # noqa: BLE001
        logger.info("Валидация завершена (метрики: %s)", metrics)

    # Копируем best.pt в models/aoi_unified.pt.
    save_dir = Path(getattr(results, "save_dir", "") or "")
    best_pt = save_dir / "weights" / "best.pt"
    if not best_pt.exists():
        # У Ultralytics 8.x save_dir иногда не заполнен в возврате.
        best_pt = Path(args.project) / args.name / "weights" / "best.pt"

    if args.no_copy:
        logger.info("--no-copy: оставляю best.pt в %s", best_pt)
    elif best_pt.exists():
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        dst = Path(args.output)
        shutil.copyfile(best_pt, dst)
        logger.info("Итоговые веса: %s", dst)
        logger.info(
            "Чтобы детектор подхватил их, укажите путь в переменной окружения "
            "AOI_MODEL_WEIGHTS_PATH=%s или активируйте как датасет в админке.",
            dst,
        )
    else:
        logger.warning("best.pt не найден (%s)", best_pt)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
