"""Агрегация оценок оператора в обучающий датасет.

После каждой ручной проверки инспекции бэкенд кладёт в ``storage/training/<id>/``
комплект файлов:

    original.<ext>           — исходное изображение без закраски
    labels.txt               — YOLO-разметка ТОЛЬКО подтверждённых дефектов
    annotations.json         — метаданные (включая class_names на момент ревью)
    false_positives/*.png    — кропы ложных срабатываний (hard negatives)
    ...

Этот скрипт сканирует каталог ``storage/training/``, читает каждую
инспекцию и добавляет её в ``datasets/unified/images|labels/{train,val}`` в
формате Ultralytics YOLO. Важные правила:

* class_id в ``labels.txt`` должны соответствовать порядку в
  ``models/unified_classes.yaml``. Если у инспекции в ``annotations.json``
  записан другой список классов, выполняется ремап по коду; если какого-то
  класса в реестре нет — предупреждение, строка пропускается.
* Инспекции БЕЗ ``labels.txt`` / БЕЗ ``original.*`` пропускаются.
* Пустой ``labels.txt`` — валидный «negative»-пример, его берём.
* Сплит train/val определяется тем же seed-алгоритмом, что и в
  ``scripts/prepare_datasets.py`` (чтобы одна и та же инспекция всегда
  попадала в один и тот же сплит при повторных сборках).

Запуск::

    python scripts/aggregate_reviews.py                    # все проверенные
    python scripts/aggregate_reviews.py --min-inspection 120
    python scripts/aggregate_reviews.py --wipe             # очистить прошлые
                                                           # «review-*» образцы
    python scripts/aggregate_reviews.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# append, а не insert(0): иначе локальный каталог datasets/ закрыл бы
# одноимённый pip-пакет для скриптов, которые его импортируют.
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.config import settings  # noqa: E402

CLASSES_YAML = ROOT / "models" / "unified_classes.yaml"
OUT_DIR = ROOT / "datasets" / "unified"

logger = logging.getLogger("aggregate_reviews")


def _clean_alias(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def _load_unified_index() -> tuple[dict[str, int], list[str]]:
    """Возвращает ``(alias_index, code_list)`` из unified_classes.yaml."""
    data = yaml.safe_load(CLASSES_YAML.read_text(encoding="utf-8"))
    alias_index: dict[str, int] = {}
    code_list: list[str] = []
    for idx, entry in enumerate(data["classes"]):
        code = entry["code"]
        code_list.append(code)
        aliases = list(entry.get("aliases") or [])
        aliases.append(code)
        for alias in aliases:
            key = _clean_alias(alias)
            if key:
                alias_index[key] = idx
    return alias_index, code_list


def _split_of(seed: str, val_frac: float) -> str:
    h = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
    return "val" if (h % 10_000) / 10_000 < val_frac else "train"


def _ensure_dirs() -> None:
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (OUT_DIR / sub).mkdir(parents=True, exist_ok=True)


def _wipe_review_samples() -> int:
    removed = 0
    for sub in ("images", "labels"):
        for split in ("train", "val"):
            for p in (OUT_DIR / sub / split).glob("review-*"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def _read_review_dir(inspection_dir: Path) -> dict | None:
    """Загружает и валидирует данные одной инспекции.

    Возвращает ``None``, если её нельзя использовать для дообучения.
    """
    labels_file = inspection_dir / "labels.txt"
    if not labels_file.exists():
        return None
    # Оригинал имеет произвольное расширение; ищем любой original.*.
    candidates = list(inspection_dir.glob("original.*"))
    if not candidates:
        return None
    original = candidates[0]

    annotations = inspection_dir / "annotations.json"
    meta: dict = {}
    if annotations.exists():
        try:
            meta = json.loads(annotations.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    return {
        "original": original,
        "labels": labels_file,
        "annotations": meta,
    }


def _remap_labels(
    labels_text: str,
    source_codes: list[str],
    alias_index: dict[str, int],
) -> tuple[list[str], Counter[str]]:
    """Перемапить class_id из старого порядка в актуальный.

    Если ``source_codes`` пустой — считаем, что class_id уже соответствует
    unified_classes.yaml (это верно для текущих 6 PCB-классов, 0..5).
    """
    out_lines: list[str] = []
    dropped: Counter[str] = Counter()
    for line in labels_text.splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        try:
            src_cls = int(parts[0])
        except ValueError:
            continue
        if source_codes:
            if not (0 <= src_cls < len(source_codes)):
                dropped[f"<id={src_cls}>"] += 1
                continue
            code = source_codes[src_cls]
            new_id = alias_index.get(_clean_alias(code))
            if new_id is None:
                dropped[code] += 1
                continue
        else:
            new_id = src_cls
        out_lines.append(f"{new_id} {' '.join(parts[1:5])}")
    return out_lines, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description="Агрегация оценок оператора в датасет")
    parser.add_argument("--val-frac", type=float, default=0.15)
    parser.add_argument(
        "--min-inspection",
        type=int,
        default=0,
        help="Брать только инспекции с id >= указанного",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Сначала удалить все существующие образцы, добавленные из ревью",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    if not CLASSES_YAML.exists():
        logger.error("Нет %s", CLASSES_YAML)
        return 1
    alias_index, code_list = _load_unified_index()
    training_root = settings.storage_dir / "training"
    if not training_root.exists():
        logger.warning("Нет каталога %s — нечего агрегировать", training_root)
        return 0

    _ensure_dirs()
    if args.wipe and not args.dry_run:
        removed = _wipe_review_samples()
        logger.info("Удалено старых review-образцов: %d", removed)

    written = 0
    skipped = 0
    empty_kept = 0
    per_class: Counter[int] = Counter()
    drops_total: Counter[str] = Counter()

    for ins_dir in sorted(training_root.iterdir()):
        if not ins_dir.is_dir():
            continue
        try:
            ins_id = int(ins_dir.name)
        except ValueError:
            continue
        if ins_id < args.min_inspection:
            continue
        payload = _read_review_dir(ins_dir)
        if payload is None:
            skipped += 1
            continue
        annotations = payload["annotations"] or {}
        source_codes = [str(x) for x in (annotations.get("class_names") or [])]

        labels_text = payload["labels"].read_text(encoding="utf-8")
        out_lines, dropped = _remap_labels(labels_text, source_codes, alias_index)
        drops_total.update(dropped)

        if not out_lines and labels_text.strip():
            # Все строки потерялись при ремапе — от такой инспекции толку нет.
            logger.warning(
                "инспекция %d: все %d строк разметки не сопоставились (%s)",
                ins_id,
                len(labels_text.splitlines()),
                dict(dropped),
            )
            skipped += 1
            continue

        stem = f"review-{ins_id:06d}"
        ext = payload["original"].suffix.lower() or ".png"
        split = _split_of(stem, args.val_frac)
        img_dst = OUT_DIR / "images" / split / f"{stem}{ext}"
        lbl_dst = OUT_DIR / "labels" / split / f"{stem}.txt"

        if args.dry_run:
            logger.info(
                "инспекция %d → %s/%s%s, меток: %d",
                ins_id,
                split,
                stem,
                ext,
                len(out_lines),
            )
        else:
            shutil.copyfile(payload["original"], img_dst)
            lbl_dst.write_text(
                "\n".join(out_lines) + ("\n" if out_lines else ""),
                encoding="utf-8",
            )
        written += 1
        if not out_lines:
            empty_kept += 1
        for line in out_lines:
            try:
                per_class[int(line.split()[0])] += 1
            except (ValueError, IndexError):
                pass

    logger.info("Обработано инспекций: %d", written + skipped)
    logger.info("Добавлено в датасет: %d (в т.ч. %d пустых — «брака нет»)", written, empty_kept)
    if skipped:
        logger.info("Пропущено: %d", skipped)
    if per_class:
        logger.info("Распределение по классам (после ремапа):")
        for cls_id, count in sorted(per_class.items()):
            code = code_list[cls_id] if cls_id < len(code_list) else f"<id={cls_id}>"
            logger.info("  %-22s  %6d", code, count)
    if drops_total:
        logger.warning(
            "Строки, потерянные при ремапе (нет в unified_classes.yaml): %s",
            dict(drops_total.most_common(20)),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
