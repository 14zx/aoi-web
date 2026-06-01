"""Сверка детекций YOLO с разметкой Golden Board (regions в payload_json).

После ECC-выравнивания координаты ``regions`` совпадают с кадром инспекции.
Добавляет синтетические дефекты:

* ``golden_component_missing`` — в зоне эталона ожидаемый класс не найден;
* ``golden_component_wrong`` — найден другой класс, чем указан в разметке.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import settings
from .class_semantics import load_mappings, semantic_kind_for_class
from .detector import DetectedDefect, NAME_BY_CODE, _normalize_class_code

logger = logging.getLogger(__name__)

CLASS_GOLDEN_MISSING = "golden_component_missing"
CLASS_GOLDEN_WRONG = "golden_component_wrong"

# Обратная совместимость (старые записи / тесты)
CLASS_WRONG_PACKAGE = CLASS_GOLDEN_WRONG
CLASS_MISSING_ETALON = CLASS_GOLDEN_MISSING


@dataclass(frozen=True, slots=True)
class GoldenRegion:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str | None = None


def extract_regions_from_payload(payload: dict | list) -> list[GoldenRegion]:
    """Читает ``regions`` из JSON профиля Golden Board."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("regions")
    if not isinstance(raw, list):
        return []
    out: list[GoldenRegion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            x1, y1, x2, y2 = int(item["x1"]), int(item["y1"]), int(item["x2"]), int(item["y2"])
        except (KeyError, TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        label = item.get("label")
        lab = str(label).strip() if label else None
        if lab == "":
            lab = None
        out.append(GoldenRegion(x1=x1, y1=y1, x2=x2, y2=y2, label=lab))
    return out


def extract_region_tolerance_from_payload(payload_json: str) -> int:
    """Допуск смещения bbox из JSON эталона; иначе глобальная настройка (12 px)."""
    try:
        data = json.loads(payload_json)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and data.get("region_tolerance_px") is not None:
        try:
            return max(0, min(128, int(data["region_tolerance_px"])))
        except (TypeError, ValueError):
            pass
    return max(0, min(128, int(getattr(settings, "golden_region_tolerance_px", 12))))


def extract_regions_from_payload_json(payload_json: str) -> list[GoldenRegion]:
    try:
        data = json.loads(payload_json)
    except json.JSONDecodeError:
        return []
    return extract_regions_from_payload(data if isinstance(data, dict) else {})


def _box_iou(
    ax1: int,
    ay1: int,
    ax2: int,
    ay2: int,
    bx1: int,
    by1: int,
    bx2: int,
    by2: int,
) -> float:
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    iw = max(0, x2 - x1)
    ih = max(0, y2 - y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    a_area = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    b_area = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = a_area + b_area - inter
    return float(inter / union) if union > 0 else 0.0


def _expand_region(
    region: GoldenRegion,
    *,
    tolerance_px: int,
    frame_width: int,
    frame_height: int,
) -> GoldenRegion:
    t = max(0, int(tolerance_px))
    return GoldenRegion(
        x1=max(0, region.x1 - t),
        y1=max(0, region.y1 - t),
        x2=min(frame_width, region.x2 + t),
        y2=min(frame_height, region.y2 + t),
        label=region.label,
    )


def _detection_hits_region(
    d: DetectedDefect,
    region: GoldenRegion,
    *,
    min_iou: float,
) -> bool:
    iou = _box_iou(d.x1, d.y1, d.x2, d.y2, region.x1, region.y1, region.x2, region.y2)
    if iou >= min_iou:
        return True
    cx = (d.x1 + d.x2) / 2.0
    cy = (d.y1 + d.y2) / 2.0
    return region.x1 <= cx <= region.x2 and region.y1 <= cy <= region.y2


def _normalize_expected_label(label: str | None) -> str | None:
    if not label:
        return None
    raw = str(label).strip()
    if not raw:
        return None
    norm = _normalize_class_code(raw)
    return norm or raw.lower().replace(" ", "_").replace("-", "_")


def _class_display(code: str) -> str:
    return NAME_BY_CODE.get(code, code)


def _class_matches_expected(detected_code: str, expected: str) -> bool:
    det = _normalize_expected_label(detected_code) or detected_code.strip().lower()
    exp = _normalize_expected_label(expected) or expected.strip().lower()
    return det == exp


def _etalon_defect_codes() -> frozenset[str]:
    return frozenset(
        {
            CLASS_GOLDEN_MISSING,
            CLASS_GOLDEN_WRONG,
            "component_missing",
            "component_misaligned",
            "component_wrong_package",
        }
    )


def _already_has_etalon_defect(
    defects: list[DetectedDefect],
    region: GoldenRegion,
    *,
    min_iou: float,
) -> bool:
    codes = _etalon_defect_codes()
    for d in defects:
        if d.class_code not in codes:
            continue
        if _box_iou(d.x1, d.y1, d.x2, d.y2, region.x1, region.y1, region.x2, region.y2) >= min_iou:
            return True
    return False


def _make_missing_defect(region: GoldenRegion, expected: str | None) -> DetectedDefect:
    if expected:
        name = f"Не найден компонент (эталон: {_class_display(expected)})"
    else:
        name = "Не найден компонент (эталон)"
    return DetectedDefect(
        class_code=CLASS_GOLDEN_MISSING,
        class_name=name,
        confidence=0.85,
        x1=region.x1,
        y1=region.y1,
        x2=region.x2,
        y2=region.y2,
    )


def _make_wrong_defect(region: GoldenRegion, expected: str, found: str) -> DetectedDefect:
    name = (
        f"Не тот компонент (ожидался {_class_display(expected)}, "
        f"найден {_class_display(found)})"
    )
    return DetectedDefect(
        class_code=CLASS_GOLDEN_WRONG,
        class_name=name,
        confidence=0.88,
        x1=region.x1,
        y1=region.y1,
        x2=region.x2,
        y2=region.y2,
    )


def apply_golden_region_checks(
    defects: list[DetectedDefect],
    *,
    payload_json: str,
    db: Session,
    frame_width: int,
    frame_height: int,
    golden_compare_ready: bool,
    tolerance_px: int | None = None,
) -> list[DetectedDefect]:
    """Добавляет дефекты по зонам эталона, если YOLO не подтвердил компонент."""
    if not getattr(settings, "golden_region_check_enabled", True):
        return defects

    regions = extract_regions_from_payload_json(payload_json)
    if not regions:
        return defects

    if not golden_compare_ready:
        logger.info(
            "Проверка regions Golden Board пропущена: кадр не приведён к координатам эталона "
            "(нет опорного снимка или ECC/масштаб не применены)"
        )
        return defects

    mappings = load_mappings(db)
    min_iou = float(getattr(settings, "golden_region_min_iou", 0.2))
    tol = (
        int(tolerance_px)
        if tolerance_px is not None
        else extract_region_tolerance_from_payload(payload_json)
    )
    tol = max(0, min(128, tol))
    extra: list[DetectedDefect] = []

    for region in regions:
        if region.x2 > frame_width or region.y2 > frame_height:
            logger.warning(
                "Region (%d,%d)-(%d,%d) вне кадра %dx%d — пропуск",
                region.x1,
                region.y1,
                region.x2,
                region.y2,
                frame_width,
                frame_height,
            )
            continue

        check_region = _expand_region(
            region,
            tolerance_px=tol,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        hits = [d for d in defects if _detection_hits_region(d, check_region, min_iou=min_iou)]
        expected = _normalize_expected_label(region.label)

        if expected:
            if any(_class_matches_expected(d.class_code, expected) for d in hits):
                continue
            if _already_has_etalon_defect(defects + extra, region, min_iou=min_iou):
                continue
            if hits:
                found = hits[0].class_code
                extra.append(_make_wrong_defect(region, expected, found))
            else:
                extra.append(_make_missing_defect(region, expected))
            continue

        if hits and any(
            semantic_kind_for_class(d.class_code, mappings) != "ignore" for d in hits
        ):
            continue
        if _already_has_etalon_defect(defects + extra, region, min_iou=min_iou):
            continue
        extra.append(_make_missing_defect(region, None))

    if not extra:
        return defects
    logger.info(
        "Golden Board regions: добавлено дефектов %d (погрешность %d px, min_iou=%.2f)",
        len(extra),
        tol,
        min_iou,
    )
    return list(defects) + extra
