"""Настройки системы, редактируемые администратором через UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import (
    ClassSemanticEntry,
    ClassSemanticsOut,
    ClassSemanticsUpdate,
    SettingsOut,
    SettingsUpdate,
)
from ..services.class_semantics import load_mappings, save_mappings
from ..services.detector import get_detector
from ..services.dynamic_settings import dynamic_settings
from .deps import require_admin, write_audit


router = APIRouter(prefix="/api/settings", tags=["Настройки"])


@router.get("", response_model=SettingsOut, summary="Текущие настройки системы")
def get_settings_api(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SettingsOut:
    return SettingsOut(**dynamic_settings.all(db))


@router.put("", response_model=SettingsOut, summary="Обновление настроек системы")
def update_settings_api(
    payload: SettingsUpdate,
    request: Request,
    manager: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SettingsOut:
    values = payload.model_dump(exclude_none=True)
    updated = dynamic_settings.update(db, values=values, updated_by=manager.id)
    write_audit(
        db,
        user=manager,
        action="settings_update",
        details=", ".join(f"{k}={v}" for k, v in values.items()),
        request=request,
    )
    db.commit()
    return SettingsOut(**updated)


@router.get(
    "/class-semantics",
    response_model=ClassSemanticsOut,
    summary="Классы модели и семантические метки",
)
def get_class_semantics(
    _user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ClassSemanticsOut:
    detector = get_detector()
    raw_classes = detector.get_defect_classes()
    mappings_raw = load_mappings(db)
    mappings: dict[str, ClassSemanticEntry] = {}
    for k, v in mappings_raw.items():
        if not isinstance(v, dict):
            continue
        kd = str(v.get("kind") or "defect").lower()
        if kd not in ("component", "defect", "ignore"):
            kd = "defect"
        mappings[k] = ClassSemanticEntry(
            kind=kd,  # type: ignore[arg-type]
            label=str(v.get("label") or "")[:128],
            review_required=bool(v.get("review_required", True)),
        )
    return ClassSemanticsOut(detector_classes=raw_classes, mappings=mappings)


@router.put(
    "/class-semantics",
    response_model=ClassSemanticsOut,
    summary="Сохранить семантику классов",
)
def put_class_semantics(
    payload: ClassSemanticsUpdate,
    request: Request,
    manager: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ClassSemanticsOut:
    serialized = {k: v.model_dump() for k, v in payload.mappings.items()}
    save_mappings(db, serialized, updated_by=manager.id)
    write_audit(
        db,
        user=manager,
        action="class_semantics_update",
        details=f"keys={len(serialized)}",
        request=request,
    )
    db.commit()
    return get_class_semantics(manager, db)  # type: ignore[arg-type]
