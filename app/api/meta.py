"""Служебные маршруты: информация о системе, справочники."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import effective_public_base_url, settings
from ..database import get_db
from ..services.class_semantics import load_mappings
from ..services.detector import get_detector
from ..services.dynamic_settings import dynamic_settings


router = APIRouter(prefix="/api", tags=["Служебные"])


@router.get("/health", summary="Проверка работоспособности")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta", summary="Метаданные системы")
def meta(db: Session = Depends(get_db)) -> dict:
    detector = get_detector()
    current = dynamic_settings.all(db)
    return {
        "app_name": settings.app_name,
        "app_code": settings.app_code,
        "version": settings.app_version,
        "public_base_url": effective_public_base_url(),
        "detector_backend": detector.backend,
        "max_upload_mb": settings.max_upload_mb,
        "min_image_resolution": 640,
        "defect_classes": detector.get_defect_classes(),
        "class_semantics": load_mappings(db),
        "live_analysis_interval_ms": current["live_analysis_interval_ms"],
        "live_analysis_max_side": current["live_analysis_max_side"],
        "detection_conf_threshold": current["detection_conf_threshold"],
        "detection_iou_threshold": current["detection_iou_threshold"],
    }
