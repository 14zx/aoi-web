"""Шаблон скрипта обучения YOLOv8 на датасете DeepPCB.

Предполагается, что датасет подготовлен в формате Ultralytics YOLO (структура
``images/train``, ``images/val``, ``labels/train``, ``labels/val``). Конфиг
``data.yaml`` содержит пути и перечень классов в соответствии с ТЗ п. 4.1.2.

Запуск::

    python -m scripts.train --data datasets/deeppcb/data.yaml --epochs 100

Данный файл является шаблоном для руководства программиста АОИ.01.33.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Обучение YOLOv8 на DeepPCB")
    parser.add_argument("--data", required=True, help="Путь к data.yaml")
    parser.add_argument("--model", default="yolov8n.pt", help="Базовая модель")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--project", default="runs/train")
    parser.add_argument("--name", default="aoi-deeppcb")
    args = parser.parse_args()

    try:
        from ultralytics import YOLO  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Пакет 'ultralytics' не установлен. Выполните 'pip install ultralytics'."
        ) from exc

    if not Path(args.data).exists():
        raise SystemExit(f"Не найден файл датасета: {args.data}")

    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=args.project,
        name=args.name,
        exist_ok=True,
    )
    metrics = model.val()
    print(f"mAP50={metrics.box.map50:.3f}  mAP50-95={metrics.box.map:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
