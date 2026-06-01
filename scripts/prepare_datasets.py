"""Подготовка единого обучающего датасета для модели АОИ.

Читает ``models/unified_classes.yaml``, скачивает / распаковывает источники и
собирает их в каталог ``datasets/unified/`` в формате Ultralytics YOLO:

    datasets/unified/
        data.yaml
        images/train/xxx.jpg
        images/val/yyy.jpg
        labels/train/xxx.txt
        labels/val/yyy.txt

Каждый источник описывается в списке ``SOURCES`` ниже. Поддерживаются типы:

    * ``hf_dataset`` — датасет с Hugging Face Hub (класс bbox-ов приводится
      через aliases из unified_classes.yaml).
    * ``hf_yolo_zip`` — файл ``.zip`` из HF dataset-репозитория, внутри
      которого лежит YOLO-структура (images/labels/data.yaml).
    * ``yolo_zip``    — локальный .zip файл в формате Ultralytics YOLO
      (images/, labels/, data.yaml внутри). Пригодно для Roboflow-экспортов.
    * ``yolo_dir``    — уже распакованный локальный каталог YOLO.

Если источник не найден / недоступен — его можно спокойно пропустить:
подготовка будет идти дальше по остальным. Получившийся ``data.yaml``
включит ТОЛЬКО те классы, для которых есть хоть одна аннотация. Остальные
классы можно наращивать позднее (например, через ``aggregate_reviews.py``).

Запуск::

    python scripts/prepare_datasets.py                 # всё по умолчанию
    python scripts/prepare_datasets.py --val-frac 0.15
    python scripts/prepare_datasets.py --only hripcb   # только один источник
    python scripts/prepare_datasets.py --dry-run       # показать, что будет
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import shutil
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

ROOT = Path(__file__).resolve().parent.parent

# ВАЖНО: НЕ добавляем ROOT в sys.path — иначе локальный каталог
# ``datasets/`` (куда мы собираем выборку) скроет pip-пакет ``datasets``
# от Hugging Face (тот самый ``from datasets import load_dataset``).
# Этот скрипт и так не импортирует ничего из ``app.*``.

CLASSES_YAML = ROOT / "models" / "unified_classes.yaml"
OUT_DIR = ROOT / "datasets" / "unified"
CACHE_DIR = ROOT / "datasets" / "_cache"

logger = logging.getLogger("prepare_datasets")


# ---------------------------------------------------------------------------
# Реестр классов
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnifiedClass:
    class_id: int
    code: str
    name: str
    aliases: tuple[str, ...]
    is_defect: bool
    category: str


def load_unified_classes() -> tuple[list[UnifiedClass], dict[str, int]]:
    """Загружает реестр и строит обратный индекс alias → class_id.

    Возвращает ``(classes, alias_index)``. Алиасы приведены к lowercase и
    очищены от пробелов/дефисов, чтобы быть устойчивыми к разнобою в
    публичных датасетах.
    """
    data = yaml.safe_load(CLASSES_YAML.read_text(encoding="utf-8"))
    classes: list[UnifiedClass] = []
    alias_index: dict[str, int] = {}
    for idx, entry in enumerate(data["classes"]):
        code = entry["code"]
        raw_aliases = list(entry.get("aliases") or [])
        # Сам код тоже считается алиасом.
        raw_aliases.append(code)
        cleaned: list[str] = []
        for alias in raw_aliases:
            key = _clean_alias(alias)
            if not key:
                continue
            cleaned.append(key)
            if key in alias_index and alias_index[key] != idx:
                raise ValueError(
                    f"Конфликт алиасов: '{alias}' указан у классов "
                    f"{classes[alias_index[key]].code} и {code}."
                )
            alias_index[key] = idx
        classes.append(
            UnifiedClass(
                class_id=idx,
                code=code,
                name=entry.get("name", code),
                aliases=tuple(cleaned),
                is_defect=bool(entry.get("is_defect", True)),
                category=entry.get("category", "defect"),
            )
        )
    return classes, alias_index


def _clean_alias(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


# ---------------------------------------------------------------------------
# Источники
# ---------------------------------------------------------------------------


@dataclass
class Source:
    key: str
    title: str
    kind: str  # hf_dataset | hf_yolo_zip | yolo_zip | yolo_dir
    # Для hf_dataset:
    hf_repo: str | None = None
    hf_config: str | None = None
    # Для hf_yolo_zip: путь к файлу в dataset-репозитории HF (например data/train.zip)
    hf_filename: str | None = None
    # Маппинг имён классов исходного датасета → код в unified_classes.yaml.
    # Если оставить пустым — будет использоваться автомаппинг через aliases.
    class_map: dict[str, str] = field(default_factory=dict)
    # Для yolo_zip / yolo_dir:
    path: Path | None = None
    # Для yolo_zip — URL, откуда скачать (если файла ещё нет локально).
    url: str | None = None
    # Доля данных, которую оставить (для быстрой отладки). 1.0 — всё.
    fraction: float = 1.0


SOURCES: list[Source] = [
    # keremberke/pcb-defect-segmentation — PCBA-датасет с 4 классами:
    #   dry_joint, incorrect_installation, pcb_damage, short_circuit.
    # Чтобы не зависеть от "dataset scripts", берём напрямую YOLO ZIP-файлы
    # (data/train.zip, data/valid.zip, data/test.zip) из dataset-репозитория.
    Source(
        key="keremberke_pcba_train",
        title="PCBA defects train (keremberke/pcb-defect-segmentation)",
        kind="hf_yolo_zip",
        hf_repo="keremberke/pcb-defect-segmentation",
        hf_filename="data/train.zip",
        class_map={
            "dry_joint":              "solder_cold",
            "incorrect_installation": "component_misaligned",
            "pcb_damage":             "copper",
            "short_circuit":          "short",
        },
    ),
    Source(
        key="keremberke_pcba_valid",
        title="PCBA defects valid (keremberke/pcb-defect-segmentation)",
        kind="hf_yolo_zip",
        hf_repo="keremberke/pcb-defect-segmentation",
        hf_filename="data/valid.zip",
        class_map={
            "dry_joint":              "solder_cold",
            "incorrect_installation": "component_misaligned",
            "pcb_damage":             "copper",
            "short_circuit":          "short",
        },
    ),
    Source(
        key="keremberke_pcba_test",
        title="PCBA defects test (keremberke/pcb-defect-segmentation)",
        kind="hf_yolo_zip",
        hf_repo="keremberke/pcb-defect-segmentation",
        hf_filename="data/test.zip",
        class_map={
            "dry_joint":              "solder_cold",
            "incorrect_installation": "component_misaligned",
            "pcb_damage":             "copper",
            "short_circuit":          "short",
        },
    ),
    # ampragatish/pcb-defects-dataset — parquet без script-loader-а.
    # Обычно содержит 6 bare-PCB классов (open/short/mousebite/spur/copper/pinhole),
    # которые автомаппятся через aliases в unified_classes.yaml.
    Source(
        key="ampragatish_pcb6",
        title="Bare PCB defects (ampragatish/pcb-defects-dataset)",
        kind="hf_dataset",
        hf_repo="ampragatish/pcb-defects-dataset",
        hf_config=None,
    ),
    # Слоты для локальных ZIP-ов (Roboflow-экспорты).
    # Чтобы задействовать — положите файл по указанному пути и уберите skip.
    Source(
        key="pcb_components",
        title="PCB Components (Roboflow YOLOv8 export, локальный ZIP)",
        kind="yolo_zip",
        path=CACHE_DIR / "pcb_components.zip",
        url=None,
        class_map={
            "missing":        "component_missing",
            "misaligned":     "component_misaligned",
            "wrong_orient":   "component_misaligned",
            "wrong_polarity": "component_misaligned",
        },
    ),
    Source(
        key="solder_defects",
        title="Solder joint defects (Roboflow YOLOv8 export, локальный ZIP)",
        kind="yolo_zip",
        path=CACHE_DIR / "solder_defects.zip",
        url=None,
        class_map={
            "bridge": "solder_bridge",
            "bridging": "solder_bridge",
            "cold": "solder_cold",
            "cold_solder": "solder_cold",
            "insufficient": "solder_cold",
            "insufficient_solder": "solder_cold",

            # твой новый датасет Defects.v8-set_4
            "Dry_joint": "solder_cold",
            "Incorrect_installation": "component_misaligned",
            "PCB_damage": "copper",
            "Short_circuit": "short",
        },
    ),
]


# ---------------------------------------------------------------------------
# Утилиты файлового вывода
# ---------------------------------------------------------------------------


def _ensure_dirs() -> None:
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (OUT_DIR / sub).mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _wipe_output() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    _ensure_dirs()


def _sample_name(source_key: str, original: str) -> str:
    """Уникальное имя файла = <source>__<sha1(original)[:10]>__<basename>."""
    stem = Path(original).stem
    ext = Path(original).suffix or ".jpg"
    digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:10]
    # Заменяем спецсимволы, оставляем только a-zA-Z0-9_-.
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem)
    return f"{source_key}__{digest}__{safe}{ext}"


def _split_of(seed: str, val_frac: float) -> str:
    h = int(hashlib.sha1(seed.encode("utf-8")).hexdigest(), 16)
    return "val" if (h % 10_000) / 10_000 < val_frac else "train"


# ---------------------------------------------------------------------------
# Маппинг меток
# ---------------------------------------------------------------------------


class LabelMapper:
    """Переводит имя класса из источника в unified class_id.

    Правила поиска (в указанном порядке):
      1. явный ``class_map[source_name] -> code``;
      2. автомаппинг через alias-индекс из unified_classes.yaml;
      3. возврат ``None`` (запись пропускается).
    """

    def __init__(
        self,
        source_class_map: dict[str, str],
        alias_index: dict[str, int],
        code_to_id: dict[str, int],
    ) -> None:
        # Приводим ключи source_class_map к чистому виду.
        self._explicit: dict[str, int] = {}
        for raw_src, target_code in source_class_map.items():
            key = _clean_alias(raw_src)
            if target_code not in code_to_id:
                raise ValueError(
                    f"class_map ссылается на неизвестный код '{target_code}'. "
                    f"Проверьте unified_classes.yaml."
                )
            self._explicit[key] = code_to_id[target_code]
        self._alias_index = alias_index
        self._misses: Counter[str] = Counter()

    def __call__(self, source_name: str) -> int | None:
        key = _clean_alias(source_name)
        if key in self._explicit:
            return self._explicit[key]
        if key in self._alias_index:
            return self._alias_index[key]
        # Удалим подчёркивания на всякий случай.
        compact = key.replace("_", "")
        if compact in self._alias_index:
            return self._alias_index[compact]
        self._misses[source_name] += 1
        return None

    @property
    def misses(self) -> Counter[str]:
        return self._misses


# ---------------------------------------------------------------------------
# Импорт из Hugging Face Hub
# ---------------------------------------------------------------------------


def _import_hf_dataset(
    source: Source,
    mapper: LabelMapper,
    val_frac: float,
    stats: dict[str, Any],
) -> None:
    try:
        from datasets import load_dataset  # type: ignore
    except Exception as exc:
        logger.error(
            "Не удалось импортировать 'datasets' (%s). "
            "Установите: pip install datasets",
            exc,
        )
        return

    logger.info("[%s] загрузка с Hugging Face: %s", source.key, source.hf_repo)
    try:
        ds = load_dataset(source.hf_repo, name=source.hf_config, cache_dir=str(CACHE_DIR / "hf"))
    except Exception as exc:
        msg = str(exc)
        logger.error("[%s] не удалось загрузить: %s", source.key, msg)
        if "Dataset scripts are no longer supported" in msg or "scripts are no longer" in msg:
            logger.error(
                "[%s] подсказка: в пакете 'datasets' >=4.0 отключена поддержка "
                "скриптовых датасетов. Откатитесь на 3.x, например: "
                "pip install \"datasets<4\" \"huggingface_hub<1.0\"",
                source.key,
            )
        return

    names_lookup: list[str] = []

    for split_name, split in ds.items():
        # Достаём имена классов из признаков датасета.
        if not names_lookup:
            feats = split.features
            # Обычно objects → Sequence({category: ClassLabel}).
            cls_feat = None
            if "objects" in feats:
                inner = feats["objects"].feature
                if isinstance(inner, dict) and "category" in inner:
                    cls_feat = inner["category"]
            elif "category" in feats:
                cls_feat = feats["category"]
            if cls_feat is not None and hasattr(cls_feat, "names"):
                names_lookup = list(cls_feat.names)
            logger.info("[%s] классы источника: %s", source.key, names_lookup or "(не определены)")

        logger.info(
            "[%s] split=%s, всего записей %d",
            source.key, split_name, len(split),
        )
        count = 0
        for row in split:
            count += 1
            if source.fraction < 1.0 and random.random() > source.fraction:
                continue
            _write_hf_row(row, source, names_lookup, mapper, val_frac, stats)
        stats["per_source"][source.key]["rows"] += count


def _import_hf_yolo_zip(
    source: Source,
    mapper: LabelMapper,
    val_frac: float,
    stats: dict[str, Any],
) -> None:
    """Скачивает zip из HF dataset-репозитория и импортирует как YOLO."""
    if not source.hf_repo or not source.hf_filename:
        logger.error("[%s] для hf_yolo_zip нужны hf_repo и hf_filename", source.key)
        return
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
    except Exception as exc:
        logger.error(
            "[%s] не удалось импортировать huggingface_hub (%s). "
            "Установите: pip install huggingface_hub",
            source.key, exc,
        )
        return
    logger.info(
        "[%s] загрузка zip из HF: %s :: %s",
        source.key, source.hf_repo, source.hf_filename,
    )
    try:
        cached = hf_hub_download(
            repo_id=source.hf_repo,
            filename=source.hf_filename,
            repo_type="dataset",
            cache_dir=str(CACHE_DIR / "hf"),
        )
    except Exception as exc:
        logger.error("[%s] не удалось скачать zip: %s", source.key, exc)
        return
    local_zip = CACHE_DIR / f"{source.key}.zip"
    try:
        shutil.copyfile(cached, local_zip)
    except OSError:
        # Если не удалось копировать (например, совпадают пути) — используем исходный.
        local_zip = Path(cached)
    local_source = Source(
        key=source.key,
        title=source.title,
        kind="yolo_zip",
        class_map=source.class_map,
        path=local_zip,
    )
    _import_yolo_zip(local_source, mapper, val_frac, stats)


def _write_hf_row(
    row: dict,
    source: Source,
    names_lookup: list[str],
    mapper: LabelMapper,
    val_frac: float,
    stats: dict[str, Any],
) -> None:
    image = row.get("image")
    if image is None:
        return

    # PIL.Image → W/H.
    width = getattr(image, "width", None) or row.get("width")
    height = getattr(image, "height", None) or row.get("height")
    if not width or not height:
        return

    # Разные версии HF датасетов хранят bbox либо в row['objects']{category,bbox},
    # либо плоско в row['bbox'] + row['category'].
    objects = row.get("objects")
    bboxes: list[list[float]] = []
    categories: list[int] = []
    if isinstance(objects, dict):
        bboxes = list(objects.get("bbox") or [])
        categories = list(objects.get("category") or [])
    else:
        bboxes = list(row.get("bbox") or [])
        categories = list(row.get("category") or [])

    # Переводим bbox в YOLO (xc, yc, w, h, нормированные).
    yolo_lines: list[str] = []
    for bbox, cat in zip(bboxes, categories):
        if len(bbox) != 4:
            continue
        # HF bbox обычно в формате [x, y, w, h], пиксели.
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            continue
        xc = (x + w / 2.0) / width
        yc = (y + h / 2.0) / height
        nw = w / width
        nh = h / height
        if not (0 <= xc <= 1 and 0 <= yc <= 1 and nw <= 1 and nh <= 1):
            continue

        if names_lookup and 0 <= int(cat) < len(names_lookup):
            source_name = names_lookup[int(cat)]
        else:
            source_name = str(cat)
        target_id = mapper(source_name)
        if target_id is None:
            continue
        yolo_lines.append(f"{target_id} {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}")
        stats["per_class"][target_id] += 1

    if not yolo_lines:
        return

    # Сохраняем картинку + метки.
    stem = _sample_name(source.key, f"{row.get('image_id', id(row))}")
    split = _split_of(stem, val_frac)
    img_out = OUT_DIR / "images" / split / stem
    lbl_out = OUT_DIR / "labels" / split / (Path(stem).stem + ".txt")
    try:
        image.save(img_out)
    except Exception as exc:
        logger.debug("не удалось сохранить %s: %s", stem, exc)
        return
    lbl_out.write_text("\n".join(yolo_lines) + "\n", encoding="utf-8")
    stats["per_source"][source.key]["written"] += 1


# ---------------------------------------------------------------------------
# Импорт локального YOLO-ZIP / каталога
# ---------------------------------------------------------------------------


def _import_yolo_zip(
    source: Source,
    mapper: LabelMapper,
    val_frac: float,
    stats: dict[str, Any],
) -> None:
    if source.path is None or not source.path.exists():
        if source.url:
            logger.info(
                "[%s] файл %s не найден. Можно скачать вручную: %s",
                source.key, source.path, source.url,
            )
        else:
            logger.info("[%s] пропускаем — локальный файл %s не предоставлен", source.key, source.path)
        return
    extracted = CACHE_DIR / f"_x_{source.key}"
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source.path) as zf:
        zf.extractall(extracted)
    _import_yolo_dir_inner(extracted, source, mapper, val_frac, stats)


def _import_yolo_dir(
    source: Source,
    mapper: LabelMapper,
    val_frac: float,
    stats: dict[str, Any],
) -> None:
    if source.path is None or not source.path.exists():
        logger.info("[%s] пропускаем — каталог %s отсутствует", source.key, source.path)
        return
    _import_yolo_dir_inner(source.path, source, mapper, val_frac, stats)


def _import_yolo_dir_inner(
    root: Path,
    source: Source,
    mapper: LabelMapper,
    val_frac: float,
    stats: dict[str, Any],
) -> None:
    # Ищем data.yaml и собираем names[].
    data_yaml = next(root.rglob("data.yaml"), None)
    if data_yaml is None:
        logger.error("[%s] в архиве нет data.yaml, не могу сопоставить классы", source.key)
        return
    meta = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    names = meta.get("names")
    if isinstance(names, dict):
        names = [names[k] for k in sorted(names, key=int)]
    names = list(names or [])
    logger.info("[%s] классы источника: %s", source.key, names)

    for labels_dir in root.rglob("labels"):
        for lbl_file in labels_dir.rglob("*.txt"):
            # Ищем парную картинку рядом (в images/).
            rel = lbl_file.relative_to(labels_dir)
            img_candidates = []
            images_root = labels_dir.parent / "images"
            for ext in (".jpg", ".jpeg", ".png", ".bmp"):
                img_candidates.append(images_root / rel.with_suffix(ext))
            img_path = next((p for p in img_candidates if p.exists()), None)
            if img_path is None:
                continue

            out_lines: list[str] = []
            for raw in lbl_file.read_text(encoding="utf-8").splitlines():
                parts = raw.strip().split()
                if len(parts) < 5:
                    continue
                try:
                    src_cls = int(parts[0])
                    xc, yc, w, h = (float(x) for x in parts[1:5])
                except ValueError:
                    continue
                if src_cls < 0 or src_cls >= len(names):
                    continue
                target_id = mapper(names[src_cls])
                if target_id is None:
                    continue
                out_lines.append(f"{target_id} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")
                stats["per_class"][target_id] += 1

            if not out_lines:
                continue

            stem_name = _sample_name(source.key, str(img_path.relative_to(root)))
            split = _split_of(stem_name, val_frac)
            img_dst = OUT_DIR / "images" / split / stem_name
            lbl_dst = OUT_DIR / "labels" / split / (Path(stem_name).stem + ".txt")
            shutil.copyfile(img_path, img_dst)
            lbl_dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            stats["per_source"][source.key]["written"] += 1


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка единого датасета для АОИ")
    parser.add_argument("--val-frac", type=float, default=0.15, help="Доля валидации (по умолчанию 0.15)")
    parser.add_argument("--only", action="append", help="Импортировать только указанные ключи (повторяемо)")
    parser.add_argument("--keep", action="store_true", help="Не очищать существующий datasets/unified")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет сделано")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Подробный вывод (DEBUG)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    if not CLASSES_YAML.exists():
        logger.error("Не найден %s", CLASSES_YAML)
        return 1

    classes, alias_index = load_unified_classes()
    code_to_id = {c.code: c.class_id for c in classes}

    logger.info("Единый реестр классов (%d):", len(classes))
    for cls in classes:
        logger.info("  %2d  %-22s  %s", cls.class_id, cls.code, cls.name)

    if args.dry_run:
        for src in SOURCES:
            if args.only and src.key not in args.only:
                continue
            logger.info("[%s] kind=%s  %s", src.key, src.kind, src.title)
        return 0

    if not args.keep:
        _wipe_output()
    else:
        _ensure_dirs()

    stats: dict[str, Any] = {
        "per_source": defaultdict(lambda: {"rows": 0, "written": 0}),
        "per_class": Counter(),
        "misses": Counter(),
    }

    dispatchers: dict[str, Callable[[Source, LabelMapper, float, dict], None]] = {
        "hf_dataset": _import_hf_dataset,
        "hf_yolo_zip": _import_hf_yolo_zip,
        "yolo_zip": _import_yolo_zip,
        "yolo_dir": _import_yolo_dir,
    }

    for src in SOURCES:
        if args.only and src.key not in args.only:
            continue
        mapper = LabelMapper(src.class_map, alias_index, code_to_id)
        dispatcher = dispatchers.get(src.kind)
        if dispatcher is None:
            logger.error("[%s] неизвестный тип источника: %s", src.key, src.kind)
            continue
        dispatcher(src, mapper, args.val_frac, stats)
        stats["misses"].update(mapper.misses)

    # Собираем итоговый data.yaml — включаем все классы (чтобы class_id не плыли).
    data_yaml = {
        "path": str(OUT_DIR).replace("\\", "/"),
        "train": "images/train",
        "val": "images/val",
        "names": {c.class_id: c.code for c in classes},
    }
    (OUT_DIR / "data.yaml").write_text(
        yaml.safe_dump(data_yaml, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    logger.info("=" * 60)
    logger.info("Итоги:")
    for key, info in stats["per_source"].items():
        logger.info(
            "  [%s] из источника записано картинок: %d  (просмотрено строк: %d)",
            key, info["written"], info["rows"],
        )
    logger.info("  распределение по классам:")
    for cls in classes:
        logger.info(
            "    %-22s  %6d",
            cls.code, stats["per_class"].get(cls.class_id, 0),
        )
    if stats["misses"]:
        logger.warning(
            "Непознанные имена классов (добавьте их в aliases / class_map): %s",
            dict(stats["misses"].most_common(20)),
        )
    (OUT_DIR / "_stats.json").write_text(
        json.dumps(
            {
                "per_source": dict(stats["per_source"]),
                "per_class": {
                    classes[k].code: v for k, v in stats["per_class"].items()
                },
                "misses": dict(stats["misses"]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("Готово. data.yaml: %s", OUT_DIR / "data.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
