"""Детектор объектов на печатном узле (YOLOv8 + опциональный fallback OpenCV).

Основные веса проекта: ``models/datasets/7/weights.pt`` (классы компонентов).
Коды брака сборки (нет элемента, не тот элемент, полярность) добавляет пайплайн
эталона Golden Board. Шесть кодов дефектов трассировки (open/short/…) — только
для совместимости с публичными пресетами через ``CLASS_ALIASES``.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import settings


logger = logging.getLogger(__name__)


# Базовый перечень классов дефектов согласно ТЗ п. 4.1.2
# (порядок соответствует разметке DeepPCB). Это первые 6 id расширенного
# реестра ``models/unified_classes.yaml`` — их трогать нельзя, иначе
# разметка всех уже сохранённых инспекций перестанет совпадать.
_BASE_DEFECT_CLASSES: list[dict[str, str]] = [
    {"code": "open",      "name": "Обрыв",                "color": "#FF4136"},
    {"code": "short",     "name": "Короткое замыкание",   "color": "#FFDC00"},
    {"code": "mousebite", "name": "Мышиный укус",         "color": "#2ECC40"},
    {"code": "spur",      "name": "Медная шпора",         "color": "#0074D9"},
    {"code": "copper",    "name": "Паразитная медь",      "color": "#B10DC9"},
    {"code": "pinhole",   "name": "Пропущенное отверстие", "color": "#FF851B"},
]


def _load_unified_classes() -> tuple[list[dict[str, str]], dict[str, str]] | None:
    """Пытается загрузить расширенный реестр из unified_classes.yaml.

    Возвращает ``(defect_classes, aliases)`` или ``None``, если файл
    отсутствует, битый или фича выключена в настройках. Формат
    ``defect_classes`` совместим с ``_BASE_DEFECT_CLASSES`` (поля code, name,
    color), а также содержит дополнительное поле ``is_defect``.
    """
    if not getattr(settings, "use_unified_classes", False):
        return None
    yaml_path = getattr(settings, "unified_classes_path", None)
    if not yaml_path:
        return None
    from pathlib import Path as _Path
    path = _Path(yaml_path)
    if not path.exists():
        return None
    try:
        import yaml  # type: ignore
    except ImportError:
        logger.warning("Для unified_classes.yaml нужен пакет PyYAML; fallback на базовые 6 классов")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = data.get("classes") or []
    except Exception as exc:  # noqa: BLE001
        logger.error("Не удалось разобрать %s: %s", path, exc)
        return None

    classes: list[dict[str, str]] = []
    aliases: dict[str, str] = {}
    for entry in entries:
        code = str(entry.get("code") or "").strip()
        if not code:
            continue
        classes.append(
            {
                "code": code,
                "name": str(entry.get("name") or code),
                "color": str(entry.get("color") or "#888888"),
                "category": str(entry.get("category") or "defect"),
                "is_defect": bool(entry.get("is_defect", True)),
            }
        )
        for alias in (entry.get("aliases") or []):
            key = str(alias).strip().lower().replace(" ", "_").replace("-", "_")
            if key:
                aliases[key] = code
        aliases[code] = code
    if len(classes) < len(_BASE_DEFECT_CLASSES):
        logger.warning(
            "В %s меньше классов (%d), чем в базовом списке (%d). "
            "Использую базовый список, чтобы не сломать совместимость.",
            path, len(classes), len(_BASE_DEFECT_CLASSES),
        )
        return None
    # Проверяем, что первые 6 кодов совпадают с базовым перечнем — это
    # гарантирует совместимость с ранее сохранёнными разметками/инспекциями.
    base_codes = [c["code"] for c in _BASE_DEFECT_CLASSES]
    if [c["code"] for c in classes[: len(base_codes)]] != base_codes:
        logger.error(
            "Первые %d классов в %s должны совпадать с базовыми %s. Fallback.",
            len(base_codes), path, base_codes,
        )
        return None
    logger.info("Загружен расширенный реестр классов: %d (%s)", len(classes), path.name)
    return classes, aliases


# При старте модуля решаем, какой список использовать.
_UNIFIED = _load_unified_classes()
if _UNIFIED is not None:
    DEFECT_CLASSES, _EXTRA_ALIASES = _UNIFIED
else:
    DEFECT_CLASSES = list(_BASE_DEFECT_CLASSES)
    _EXTRA_ALIASES = {}

CODE_BY_INDEX = {i: c["code"] for i, c in enumerate(DEFECT_CLASSES)}
NAME_BY_CODE = {c["code"]: c["name"] for c in DEFECT_CLASSES}
NAME_BY_CODE.setdefault("placement_tilt", "Нарушение ориентации")
NAME_BY_CODE.setdefault("solder_bridge", "Перемычка припоя")
NAME_BY_CODE.setdefault("golden_component_missing", "Не найден компонент (эталон)")
NAME_BY_CODE.setdefault("golden_component_wrong", "Не тот компонент (эталон)")
NAME_BY_CODE.setdefault("golden_polarity_wrong", "Неверная полярность (эталон)")
NAME_BY_CODE.setdefault("component_wrong_package", "Не тот компонент (эталон)")

# Таблица синонимов: разные авторы публичных весов используют разные варианты
# написания классов (PKU-Market-PCB, DeepPCB, HRIPCB и т. д.). Приводим к
# нашему внутреннему коду. Ключи — в нижнем регистре, без пробелов и дефисов.
CLASS_ALIASES: dict[str, str] = {
    # open
    "open": "open",
    "open_circuit": "open",
    "opencircuit": "open",
    "broken": "open",
    "trace_break": "open",
    # short
    "short": "short",
    "short_circuit": "short",
    "shortcircuit": "short",
    # mousebite
    "mousebite": "mousebite",
    "mouse_bite": "mousebite",
    "mouse-bite": "mousebite",
    # spur
    "spur": "spur",
    # copper (spurious copper)
    "copper": "copper",
    "spurious_copper": "copper",
    "spuriouscopper": "copper",
    "excess_copper": "copper",
    # pinhole / missing hole
    "pinhole": "pinhole",
    "pin_hole": "pinhole",
    "pin-hole": "pinhole",
    "missing_hole": "pinhole",
    "missinghole": "pinhole",
    "hole_missing": "pinhole",
    "placement_tilt": "placement_tilt",
    "tilt": "placement_tilt",
    "misaligned": "placement_tilt",
}

# Если загружен расширенный реестр — добавляем его алиасы (они не должны
# конфликтовать с базовыми). Дубликаты разрешаем в пользу уже
# имеющихся значений, чтобы не сломать нормализацию для базовых кодов.
for _alias, _code in _EXTRA_ALIASES.items():
    CLASS_ALIASES.setdefault(_alias, _code)
    CLASS_ALIASES.setdefault(_alias.replace("_", ""), _code)


def _normalize_class_code(raw: str | None) -> str | None:
    """Нормализует произвольное имя класса к внутреннему коду.

    Возвращает ``None``, если имя не распознано.
    """
    if not raw:
        return None
    key = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    # Иногда встречается слитное написание — приводим к общему виду.
    key_compact = key.replace("_", "")
    if key in CLASS_ALIASES:
        return CLASS_ALIASES[key]
    if key_compact in CLASS_ALIASES:
        return CLASS_ALIASES[key_compact]
    if key in NAME_BY_CODE:
        return key
    return None


def _sanitize_class_code(raw: str) -> str:
    """Преобразует произвольный label в безопасный внутренний код."""
    code = raw.strip().lower().replace(" ", "_").replace("-", "_")
    code = "".join(ch for ch in code if ch.isalnum() or ch == "_")
    while "__" in code:
        code = code.replace("__", "_")
    return code.strip("_") or "unknown"


def _color_from_code(code: str) -> str:
    """Стабильный цвет по коду класса (hex)."""
    digest = hashlib.md5(code.encode("utf-8")).hexdigest()
    # Делаем цвет не слишком тёмным, чтобы bbox были читаемыми.
    r = 80 + (int(digest[0:2], 16) % 150)
    g = 80 + (int(digest[2:4], 16) % 150)
    b = 80 + (int(digest[4:6], 16) % 150)
    return f"#{r:02X}{g:02X}{b:02X}"


def registry_class_is_defect(class_code: str) -> bool:
    """``True``, если код в реестре помечен как дефект (``is_defect``), иначе компонент/контекст."""
    norm = _normalize_class_code(class_code) or str(class_code or "").strip().lower()
    if not norm:
        return True
    for entry in DEFECT_CLASSES:
        if entry.get("code") == norm:
            return bool(entry.get("is_defect", True))
    return True


@dataclass(slots=True)
class DetectedDefect:
    """Единичный обнаруженный дефект.

    ``polygon`` — контур сегментации (список точек ``(x, y)`` в пикселях
    исходного кадра), если модель YOLO-seg вернула маску. Используется для
    обводки «пиксель-в-пиксель» и точной оценки наклона. ``None`` — у bbox-моделей
    и у синтетических дефектов постобработки.
    """

    class_code: str
    class_name: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int
    polygon: list[tuple[int, int]] | None = None


@dataclass(slots=True)
class DetectionResult:
    """Результат одного запуска детектора."""

    defects: list[DetectedDefect]
    inference_time_ms: float
    backend: str  # 'yolov8' | 'fallback'
    used_tiling: bool = False


def _iou_xyxy(a: DetectedDefect, b: DetectedDefect) -> float:
    x1 = max(a.x1, b.x1)
    y1 = max(a.y1, b.y1)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    iw = max(0, x2 - x1)
    ih = max(0, y2 - y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    a_area = max(0, a.x2 - a.x1) * max(0, a.y2 - a.y1)
    b_area = max(0, b.x2 - b.x1) * max(0, b.y2 - b.y1)
    union = a_area + b_area - inter
    return float(inter / union) if union > 0 else 0.0


def _nms_defects(defects: list[DetectedDefect], iou_thresh: float) -> list[DetectedDefect]:
    by_class: dict[str, list[DetectedDefect]] = {}
    for d in defects:
        by_class.setdefault(d.class_code, []).append(d)
    out: list[DetectedDefect] = []
    for _cls, group in by_class.items():
        group = sorted(group, key=lambda x: -x.confidence)
        kept: list[DetectedDefect] = []
        for d in group:
            if any(_iou_xyxy(d, k) > iou_thresh for k in kept):
                continue
            kept.append(d)
        out.extend(kept)
    return out


class Detector:
    """Обёртка над моделью детекции. Инкапсулирует выбор backend-а."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._backend = "fallback"
        self._weights_path: Path | None = None
        self._runtime_classes: list[dict[str, str]] = list(DEFECT_CLASSES)
        # Загрузка весов — в lifespan (dataset_manager) после БД, чтобы не трогать
        # отсутствующий MODEL_WEIGHTS_PATH, если будет активный датасет.

    # --------------------------------------------------------------
    # Внутреннее
    # --------------------------------------------------------------
    def _load_model(
        self,
        weights: Path | None = None,
        *,
        weights_label: str | None = None,
    ) -> None:
        """Загружает веса. Если ``weights`` не указан, берёт путь из настроек.

        ``weights_label`` — подпись для лога (например имя датасета из БД).
        """
        from_settings = weights is None
        if weights is None:
            weights = Path(settings.model_weights_path)
        if not weights.exists():
            if from_settings:
                logger.debug("MODEL_WEIGHTS_PATH не найден (%s), YOLO не загружен.", weights)
            else:
                logger.warning(
                    "Файл весов не найден по пути %s — используется fallback-детектор.",
                    weights,
                )
            return
        try:
            from ultralytics import YOLO  # type: ignore
        except Exception as exc:  # pragma: no cover - зависит от окружения
            logger.warning(
                "Пакет 'ultralytics' недоступен (%s). Используется fallback-детектор.",
                exc,
            )
            return
        try:
            self._model = YOLO(str(weights))
            self._backend = "yolov8"
            self._weights_path = weights
            self._sync_runtime_classes()
            if weights_label:
                logger.info(
                    'Загружены веса YOLOv8, датасет «%s»: %s',
                    weights_label,
                    weights,
                )
            else:
                logger.info("Загружены веса YOLOv8 из %s", weights)
        except Exception as exc:  # pragma: no cover - зависит от окружения
            logger.error("Ошибка загрузки весов YOLOv8 (%s). Используется fallback.", exc)
            self._model = None
            self._runtime_classes = list(DEFECT_CLASSES)

    def _sync_runtime_classes(self) -> None:
        """Синхронизирует справочник классов с именами текущей модели."""
        if self._model is None:
            self._runtime_classes = list(DEFECT_CLASSES)
            return
        names = getattr(self._model, "names", None)
        if not names:
            self._runtime_classes = list(DEFECT_CLASSES)
            return

        # Ultralytics может вернуть dict[int, str] или list[str].
        if isinstance(names, dict):
            ordered = [str(names[k]) for k in sorted(names.keys())]
        else:
            ordered = [str(v) for v in names]

        classes: list[dict[str, str]] = []
        seen_codes: set[str] = set()
        for idx, raw in enumerate(ordered):
            normalized = _normalize_class_code(raw)
            if normalized is not None:
                code = normalized
                name = NAME_BY_CODE.get(code, raw)
                color = next((c["color"] for c in DEFECT_CLASSES if c["code"] == code), _color_from_code(code))
            else:
                code = _sanitize_class_code(raw)
                if not code or code in seen_codes:
                    code = f"class_{idx}"
                name = raw or code
                color = _color_from_code(code)
            seen_codes.add(code)
            classes.append({"code": code, "name": name, "color": color})

        self._runtime_classes = classes or list(DEFECT_CLASSES)

    def reload(
        self,
        weights_path: str | Path | None,
        *,
        weights_label: str | None = None,
    ) -> str:
        """Перезагружает модель с новым файлом весов.

        ``weights_label`` — человекочитаемое имя (например название датасета) для лога.

        :return: имя использованного backend (``yolov8`` или ``fallback``).
        """
        with self._lock:
            self._model = None
            self._backend = "fallback"
            self._weights_path = None
            self._runtime_classes = list(DEFECT_CLASSES)
            if weights_path:
                self._load_model(Path(weights_path), weights_label=weights_label)
            return self._backend

    @property
    def weights_path(self) -> Path | None:
        return self._weights_path

    # --------------------------------------------------------------
    # Публичный API
    # --------------------------------------------------------------
    @property
    def backend(self) -> str:
        return self._backend

    def get_defect_classes(self) -> list[dict[str, str]]:
        """Возвращает текущий справочник классов (из активной модели или fallback)."""
        with self._lock:
            return list(self._runtime_classes)

    def predict(
        self,
        image_rgb: np.ndarray,
        *,
        conf_threshold: float | None = None,
        iou_threshold: float | None = None,
    ) -> DetectionResult:
        """Выполняет детекцию на RGB-изображении.

        :param conf_threshold: переопределение порога достоверности (0..1).
        :param iou_threshold: переопределение порога NMS по IoU (0..1).
        """
        conf = float(conf_threshold if conf_threshold is not None else settings.detection_conf_threshold)
        iou = float(iou_threshold if iou_threshold is not None else settings.detection_iou_threshold)
        used_tiling = False
        with self._lock:
            start = time.perf_counter()
            if self._model is not None:
                h, w = image_rgb.shape[:2]
                max_side = max(h, w)
                tile = int(settings.detection_tile_size)
                if (
                    bool(getattr(settings, "detection_tiling_enabled", False))
                    and max_side >= int(settings.detection_tiling_min_side)
                    and min(h, w) >= tile // 2
                ):
                    defects = self._predict_yolo_tiled(image_rgb, conf=conf, iou=iou)
                    used_tiling = True
                else:
                    # Анализ всей платы одним кадром: imgsz подбираем под размер
                    # снимка (кратно 32), но не больше потолка и не апскейлим.
                    imgsz = self._full_image_imgsz(max_side)
                    defects = self._predict_yolo(image_rgb, conf=conf, iou=iou, imgsz=imgsz)
                backend = "yolov8"
            else:
                defects = self._predict_fallback(image_rgb, conf=conf)
                backend = "fallback"
            elapsed = (time.perf_counter() - start) * 1000.0
        return DetectionResult(
            defects=defects,
            inference_time_ms=elapsed,
            backend=backend,
            used_tiling=used_tiling,
        )

    @staticmethod
    def _full_image_imgsz(max_side: int) -> int:
        """imgsz для анализа всего кадра: кратно 32, не больше потолка, без апскейла."""
        cap = int(getattr(settings, "detection_full_image_imgsz", 1280))
        target = min(int(max_side), cap)
        target = max(320, target)
        # Округляем вверх до кратного 32 (требование сетки YOLO).
        return int(((target + 31) // 32) * 32)

    def _predict_yolo_tiled(
        self,
        image_rgb: np.ndarray,
        *,
        conf: float,
        iou: float,
    ) -> list[DetectedDefect]:
        """Скользящее окно по крупному снимку + NMS (дубликаты на границах плиток)."""
        assert self._model is not None
        tile = max(320, int(settings.detection_tile_size))
        overlap = float(settings.detection_tile_overlap)
        overlap = min(0.45, max(0.05, overlap))
        stride = max(32, int(tile * (1.0 - overlap)))
        h, w = image_rgb.shape[:2]
        merged: list[DetectedDefect] = []
        y = 0
        while y < h:
            x = 0
            while x < w:
                x2 = min(x + tile, w)
                y2 = min(y + tile, h)
                if x2 - x < 32 or y2 - y < 32:
                    break
                patch = image_rgb[y:y2, x:x2]
                sub = self._predict_yolo(patch, conf=conf, iou=iou, imgsz=tile)
                for d in sub:
                    shifted_poly = (
                        [(px + x, py + y) for (px, py) in d.polygon]
                        if d.polygon
                        else None
                    )
                    merged.append(
                        DetectedDefect(
                            class_code=d.class_code,
                            class_name=d.class_name,
                            confidence=d.confidence,
                            x1=d.x1 + x,
                            y1=d.y1 + y,
                            x2=d.x2 + x,
                            y2=d.y2 + y,
                            polygon=shifted_poly,
                        )
                    )
                x += stride
                if x2 >= w:
                    break
            y += stride
            if y >= h:
                break
        return _nms_defects(merged, iou_thresh=max(0.25, min(0.85, iou)))

    # --------------------------------------------------------------
    # YOLOv8
    # --------------------------------------------------------------
    def _predict_yolo(
        self,
        image_rgb: np.ndarray,
        *,
        conf: float,
        iou: float,
        imgsz: int | None = None,
    ) -> list[DetectedDefect]:
        assert self._model is not None
        results = self._predict_with_task_fallback(
            source=image_rgb,
            conf=conf,
            iou=iou,
            imgsz=imgsz,
        )
        defects: list[DetectedDefect] = []
        if not results:
            return defects
        r = results[0]
        names = getattr(r, "names", None) or getattr(self._model, "names", None) or CODE_BY_INDEX
        boxes = getattr(r, "boxes", None)
        if boxes is None or boxes.xyxy is None:
            return defects

        xyxy = boxes.xyxy.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        box_conf = boxes.conf.cpu().numpy()
        polygons = self._extract_polygons(r, len(xyxy))
        for i in range(len(xyxy)):
            if isinstance(names, dict):
                code_raw = names.get(int(cls[i]))
            elif isinstance(names, list) and 0 <= int(cls[i]) < len(names):
                code_raw = names[int(cls[i])]
            else:
                code_raw = str(cls[i])
            code = _normalize_class_code(code_raw)
            if code is not None:
                name = NAME_BY_CODE.get(code, str(code_raw) if code_raw else code)
            elif isinstance(code_raw, str) and code_raw.strip():
                code = _sanitize_class_code(code_raw)
                name = code_raw.strip()
            else:
                # Если имя совсем недоступно, используем индекс как нейтральный код.
                code = CODE_BY_INDEX.get(int(cls[i]), f"class_{int(cls[i])}")
                name = NAME_BY_CODE.get(code, code)
            x1, y1, x2, y2 = xyxy[i].tolist()
            defects.append(
                DetectedDefect(
                    class_code=code,
                    class_name=name,
                    confidence=float(box_conf[i]),
                    x1=int(round(x1)),
                    y1=int(round(y1)),
                    x2=int(round(x2)),
                    y2=int(round(y2)),
                    polygon=polygons[i] if i < len(polygons) else None,
                )
            )
        return defects

    @staticmethod
    def _extract_polygons(result, n_boxes: int) -> list[list[tuple[int, int]] | None]:
        """Достаёт контуры сегментации (masks.xy) в порядке боксов.

        Возвращает список длиной ``n_boxes`` (``None`` там, где маски нет либо
        модель не сегментационная). Контуры упрощаются (approxPolyDP), чтобы не
        раздувать БД и JSON-ответ.
        """
        out: list[list[tuple[int, int]] | None] = [None] * n_boxes
        masks = getattr(result, "masks", None)
        if masks is None:
            return out
        xy = getattr(masks, "xy", None)
        if not xy:
            return out
        for i, poly in enumerate(xy):
            if i >= n_boxes:
                break
            try:
                pts = np.asarray(poly, dtype=np.float32)
                if pts.ndim != 2 or pts.shape[0] < 3:
                    continue
                eps = 0.004 * cv2.arcLength(pts.reshape(-1, 1, 2), True)
                approx = cv2.approxPolyDP(pts.reshape(-1, 1, 2), max(1.0, eps), True)
                out[i] = [(int(round(p[0][0])), int(round(p[0][1]))) for p in approx]
            except Exception:  # noqa: BLE001 — маска не критична, пропускаем
                out[i] = None
        return out

    def _predict_with_task_fallback(
        self,
        *,
        source: np.ndarray,
        conf: float,
        iou: float,
        imgsz: int | None = None,
    ):
        """Запускает predict как в yolo_server.py с task-fallback."""
        assert self._model is not None
        predict_kwargs: dict = {"conf": conf, "iou": iou, "verbose": False}
        if imgsz:
            predict_kwargs["imgsz"] = int(imgsz)
        try:
            return self._model.predict(source=source, **predict_kwargs)
        except Exception as exc:
            if "object has no attribute 'shape'" not in str(exc):
                raise
            logger.warning(
                "Обнаружен task mismatch в YOLO-предикторе, пробую fallback task detect/segment: %s",
                exc,
            )
            if self._weights_path is None:
                raise
            from ultralytics import YOLO  # type: ignore
            last_exc = exc
            for forced_task in ("detect", "segment"):
                try:
                    test_model = YOLO(str(self._weights_path))
                    test_model.task = forced_task
                    test_model.overrides["task"] = forced_task
                    test_model.predictor = None
                    if getattr(test_model, "model", None) is not None:
                        try:
                            test_model.model.task = forced_task
                        except Exception:
                            pass
                    out = test_model.predict(source=source, **predict_kwargs)
                    self._model = test_model
                    self._sync_runtime_classes()
                    logger.info("YOLO fallback успешно восстановлен с task='%s'", forced_task)
                    return out
                except Exception as fallback_exc:
                    last_exc = fallback_exc
                    logger.warning("YOLO fallback task='%s' не сработал: %s", forced_task, fallback_exc)
            raise last_exc

    # --------------------------------------------------------------
    # Fallback-эвристика (OpenCV)
    # --------------------------------------------------------------
    def _predict_fallback(
        self,
        image_rgb: np.ndarray,
        *,
        conf: float,
    ) -> list[DetectedDefect]:
        """Простейшая эвристическая заглушка.

        Ищет контрастные локальные аномалии по разности с размытой версией
        изображения и возвращает до 5 крупнейших областей. Только для демо!
        """
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (21, 21), 0)
        diff = cv2.absdiff(gray, blurred)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

        h, w = gray.shape[:2]
        min_area = (h * w) * 0.0005

        defects: list[DetectedDefect] = []
        for i, cnt in enumerate(contours):
            area = cv2.contourArea(cnt)
            if area < min_area:
                continue
            confidence = 0.5 + 0.1 * (i % 3)
            if confidence < conf:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            cls_info = DEFECT_CLASSES[i % len(DEFECT_CLASSES)]
            defects.append(
                DetectedDefect(
                    class_code=cls_info["code"],
                    class_name=cls_info["name"],
                    confidence=confidence,
                    x1=x,
                    y1=y,
                    x2=x + bw,
                    y2=y + bh,
                )
            )
        return defects


# -----------------------------------------------------------------
# Singleton
# -----------------------------------------------------------------
_detector: Detector | None = None
_detector_lock = threading.Lock()


def get_detector() -> Detector:
    """Возвращает единственный экземпляр детектора (lazy-инициализация)."""
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                _detector = Detector()
    return _detector
