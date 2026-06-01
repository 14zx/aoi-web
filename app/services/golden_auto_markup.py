"""Авторазметка Golden Board: YOLO на опорном снимке → regions с классами."""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from .class_semantics import load_mappings, semantic_kind_for_class
from .detector import DEFECT_CLASSES, DetectedDefect, get_detector
from .dynamic_settings import dynamic_settings

logger = logging.getLogger(__name__)

# Чисто дефектные классы платы — на эталоне «хорошей» платы в разметку не включаем.
_PCB_SURFACE_DEFECTS = frozenset(
    {"open", "short", "mousebite", "spur", "copper", "pinhole", "solder_bridge", "solder_cold"}
)

_COMPONENT_CATEGORIES = frozenset({"component", "solder"})


def _is_component_like(class_code: str, mappings: dict) -> bool:
    kind = semantic_kind_for_class(class_code, mappings)
    if kind == "component":
        return True
    if kind == "defect" and class_code in _PCB_SURFACE_DEFECTS:
        return False
    if kind == "ignore":
        return False
    for entry in DEFECT_CLASSES:
        if str(entry.get("code")) == class_code:
            cat = str(entry.get("category") or "").lower()
            if cat in _COMPONENT_CATEGORIES:
                return True
            if cat == "defect" and class_code not in _PCB_SURFACE_DEFECTS:
                return True
    code = class_code.strip().lower()
    if code.startswith("component"):
        return True
    return code not in _PCB_SURFACE_DEFECTS


def detections_to_golden_regions(
    defects: list[DetectedDefect],
    *,
    mappings: dict,
    min_box: int = 8,
) -> list[dict]:
    """Преобразует детекции в список ``regions`` для payload_json."""
    out: list[dict] = []
    for d in defects:
        if not _is_component_like(d.class_code, mappings):
            continue
        w = d.x2 - d.x1
        h = d.y2 - d.y1
        if w < min_box or h < min_box:
            continue
        out.append(
            {
                "x1": int(d.x1),
                "y1": int(d.y1),
                "x2": int(d.x2),
                "y2": int(d.y2),
                "label": d.class_code,
            }
        )
    return out


def auto_markup_regions_from_rgb(rgb: np.ndarray, db: Session) -> list[dict]:
    """Запускает YOLO на опорном снимке и возвращает regions."""
    conf = float(dynamic_settings.get(db, "detection_conf_threshold"))
    iou = float(dynamic_settings.get(db, "detection_iou_threshold"))
    detector = get_detector()
    result = detector.predict(rgb, conf_threshold=conf, iou_threshold=iou)
    mappings = load_mappings(db)
    regions = detections_to_golden_regions(result.defects, mappings=mappings)
    logger.info(
        "Golden auto-markup: %d regions из %d детекций (backend=%s)",
        len(regions),
        len(result.defects),
        result.backend,
    )
    return regions
