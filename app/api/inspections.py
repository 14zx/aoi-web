"""Маршруты работы с инспекциями (ТЗ Ф2-Ф8, Ф10).

Основной сценарий:

1. Оператор отправляет POST /api/inspections с файлом изображения.
2. Сервер выполняет предобработку, детекцию, визуализацию и сохраняет
   протокол в БД. Исходное и результирующее изображения сохраняются на
   диск в ``storage_dir``.
3. Клиент получает структуру ``InspectionDetailOut`` с URL результата.
4. Протоколы доступны через /api/inspections/{id}/export/{pdf|csv}.

Отдельно реализован «живой» анализ (/api/inspections/live): принимает один
кадр, выполняет детекцию и возвращает список дефектов в JSON без сохранения
на диск и без записи в БД — для наложения рамок поверх видеопотока с
мобильного устройства.

Разграничение доступа (ТЗ 4.8.3): оператор видит только собственные
инспекции; руководитель — все.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..database import get_db
from ..models import Defect, Device, GoldenBoardProfile, Inspection, InspectionStatus, User, UserRole
from ..schemas import (
    CONFIRM_PURGE_ALL_INSPECTIONS,
    DefectOut,
    InspectionDetailOut,
    InspectionListItem,
    InspectionReviewIn,
    LiveDetectionResult,
    PurgeAllInspectionsIn,
    PurgeAllInspectionsOut,
)
from ..services import (
    DEFECT_CLASSES,
    generate_csv_report,
    generate_pdf_report,
    get_detector,
    load_image,
    render_result_image,
)
from ..services.detector import DetectedDefect
from ..services.visualization import render_masked_defect_protocol
from ..services.dynamic_settings import dynamic_settings
from ..services.class_semantics import (
    auto_real_defect_if_unreviewed,
    counts_as_protocol_defect,
    load_mappings,
    semantic_kind_for_class,
)
from ..services.golden_alignment import GoldenAlignResult, align_rgb_with_golden_profile
from ..services.golden_polarity_check import (
    apply_golden_polarity_checks,
    load_reference_rgb_from_payload,
)
from ..services.golden_region_check import apply_golden_region_checks
from ..services.post_detection import apply_component_tilt_rules
from ..services.preprocessing import ImageValidationError, apply_detection_preprocess
from .deps import has_backoffice_role, require_admin, require_any, require_manager, write_audit


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inspections", tags=["Инспекции"])


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def _to_detail(inspection: Inspection, db: Session) -> InspectionDetailOut:
    sem_map = load_mappings(db)
    defects_sorted = sorted(inspection.defects, key=lambda d: d.id)
    return InspectionDetailOut(
        id=inspection.id,
        operator_id=inspection.operator_id,
        operator_username=inspection.operator.username if inspection.operator else None,
        device_id=inspection.device_id,
        device_name=inspection.device.name if inspection.device else None,
        original_filename=inspection.original_filename,
        board_model=inspection.board_model,
        golden_board_profile_id=inspection.golden_board_profile_id,
        golden_alignment_used=inspection.golden_alignment_used,
        alignment_mae_before=inspection.alignment_mae_before,
        alignment_mae_after=inspection.alignment_mae_after,
        original_url=f"/api/inspections/{inspection.id}/image?kind=original",
        result_url=(
            f"/api/inspections/{inspection.id}/image?kind=result"
            if inspection.result_path
            else None
        ),
        image_width=inspection.image_width,
        image_height=inspection.image_height,
        status=inspection.status,
        defects_count=inspection.defects_count,
        detections_count=len(defects_sorted),
        avg_confidence=inspection.avg_confidence,
        inference_time_ms=inspection.inference_time_ms,
        conf_threshold=inspection.conf_threshold,
        notes=inspection.notes,
        error_message=inspection.error_message,
        training_dir=inspection.training_dir,
        created_at=inspection.created_at,
        reviewed_at=inspection.reviewed_at,
        defects=[
            DefectOut(
                id=d.id,
                class_code=d.class_code,
                class_name=d.class_name,
                confidence=d.confidence,
                bbox_x1=d.bbox_x1,
                bbox_y1=d.bbox_y1,
                bbox_x2=d.bbox_x2,
                bbox_y2=d.bbox_y2,
                is_reviewed=d.is_reviewed,
                is_real_defect=d.is_real_defect,
                semantic_kind=semantic_kind_for_class(d.class_code, sem_map),
                exclude_from_training=bool(getattr(d, "exclude_from_training", False)),
            )
            for d in defects_sorted
        ],
    )


def _load_inspection_full(db: Session, inspection_id: int) -> Inspection | None:
    return db.execute(
        select(Inspection)
        .options(
            selectinload(Inspection.defects),
            selectinload(Inspection.operator),
            selectinload(Inspection.device),
        )
        .where(Inspection.id == inspection_id)
    ).scalar_one_or_none()


def _check_access(inspection: Inspection, user: User) -> None:
    if has_backoffice_role(user):
        return
    if inspection.operator_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа")


def _remove_inspection_artifacts(inspection: Inspection) -> None:
    """Удаляет файлы инспекции в storage (оригинал, результат, каталог дообучения)."""
    for rel in (inspection.original_path, inspection.result_path):
        if not rel:
            continue
        path = settings.storage_dir / rel
        if path.exists():
            try:
                path.unlink()
            except OSError:
                logger.warning("Не удалось удалить файл %s", path)
    if inspection.training_dir:
        tdir = settings.storage_dir / inspection.training_dir
        if tdir.is_dir():
            shutil.rmtree(tdir, ignore_errors=True)


def _resolve_device(
    db: Session, *, device_id: int | None, user: User
) -> Device | None:
    """Проверяет, что устройство можно использовать текущим оператором."""
    if device_id is None:
        return None
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено")
    if not device.is_active:
        raise HTTPException(status_code=400, detail="Устройство неактивно")
    if (
        not has_backoffice_role(user)
        and device.assigned_operator_id is not None
        and device.assigned_operator_id != user.id
    ):
        raise HTTPException(
            status_code=403,
            detail="Устройство занято другим оператором",
        )
    return device


def _resolve_golden_board_profile(
    db: Session,
    *,
    golden_board_profile_id: int | None,
    user: User,
) -> None:
    if golden_board_profile_id is None:
        return
    if golden_board_profile_id < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="golden_board_profile_id должен быть положительным целым",
        )
    profile = db.get(GoldenBoardProfile, golden_board_profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Профиль эталона не найден",
        )
    if user.role == UserRole.OPERATOR and profile.designated_operator_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Этот эталон не закреплён за вами. Обратитесь к руководителю.",
        )


def _maybe_align_with_golden_profile(
    db: Session,
    rgb: np.ndarray,
    golden_board_profile_id: int | None,
) -> tuple[np.ndarray, GoldenAlignResult | None]:
    """Опционально выравнивает кадр по эталону Golden Board (ECC)."""
    if golden_board_profile_id is None:
        return rgb, None
    if golden_board_profile_id < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="golden_board_profile_id должен быть положительным целым",
        )
    profile = db.get(GoldenBoardProfile, golden_board_profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Профиль эталона не найден",
        )
    meta = align_rgb_with_golden_profile(
        rgb,
        payload_json=profile.payload_json,
        settings=settings,
    )
    if meta.detail:
        logger.info(
            "Эталон golden_board_profile_id=%s: %s",
            golden_board_profile_id,
            meta.detail,
        )
    return meta.rgb, meta


def _apply_golden_region_checks(
    db: Session,
    defects: list[DetectedDefect],
    *,
    golden_board_profile_id: int | None,
    gold_meta: GoldenAlignResult | None,
    frame_width: int,
    frame_height: int,
    tolerance_px: int | None = None,
) -> list[DetectedDefect]:
    """Сверка детекций с разметкой regions профиля Golden Board."""
    if golden_board_profile_id is None:
        return defects
    profile = db.get(GoldenBoardProfile, golden_board_profile_id)
    if profile is None:
        return defects
    return apply_golden_region_checks(
        defects,
        payload_json=profile.payload_json,
        db=db,
        frame_width=frame_width,
        frame_height=frame_height,
        golden_compare_ready=bool(gold_meta and gold_meta.compare_ready),
        tolerance_px=tolerance_px,
    )


def _apply_golden_polarity_checks(
    db: Session,
    defects: list[DetectedDefect],
    *,
    inspection_rgb: np.ndarray,
    golden_board_profile_id: int | None,
    gold_meta: GoldenAlignResult | None,
    frame_width: int,
    frame_height: int,
) -> list[DetectedDefect]:
    if golden_board_profile_id is None:
        return defects
    profile = db.get(GoldenBoardProfile, golden_board_profile_id)
    if profile is None:
        return defects
    reference_rgb = load_reference_rgb_from_payload(profile.payload_json, settings.storage_dir)
    return apply_golden_polarity_checks(
        defects,
        inspection_rgb=inspection_rgb,
        reference_rgb=reference_rgb,
        payload_json=profile.payload_json,
        db=db,
        frame_width=frame_width,
        frame_height=frame_height,
        golden_compare_ready=bool(gold_meta and gold_meta.compare_ready),
    )


# ---------------------------------------------------------------------------
# Создание инспекции (Ф2/Ф3/Ф4/Ф5/Ф6/Ф7)
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=InspectionDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Выполнение инспекции (Ф2–Ф7)",
)
async def create_inspection(
    request: Request,
    image: UploadFile = File(..., description="JPEG/PNG изображение платы"),
    notes: str | None = Form(default=None, max_length=2000),
    board_model: str | None = Form(default=None, max_length=255),
    golden_board_profile_id: int | None = Form(default=None),
    device_id: int | None = Form(default=None),
    conf_threshold: float | None = Form(default=None, ge=0.0, le=1.0),
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> InspectionDetailOut:
    device = _resolve_device(db, device_id=device_id, user=user)
    _resolve_golden_board_profile(
        db, golden_board_profile_id=golden_board_profile_id, user=user
    )

    raw = await image.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Файл пуст")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Размер файла превышает {settings.max_upload_mb} Мбайт",
        )
    if image.content_type and image.content_type not in {
        "image/jpeg",
        "image/png",
        "image/jpg",
    }:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Поддерживаются только JPEG и PNG изображения",
        )

    try:
        rgb = load_image(raw)
    except ImageValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    rgb = apply_detection_preprocess(rgb)
    rgb, gold_meta = _maybe_align_with_golden_profile(db, rgb, golden_board_profile_id)

    h, w = rgb.shape[:2]
    effective_conf = (
        conf_threshold
        if conf_threshold is not None
        else float(dynamic_settings.get(db, "detection_conf_threshold"))
    )
    effective_iou = float(dynamic_settings.get(db, "detection_iou_threshold"))

    uid = uuid.uuid4().hex
    safe_ext = Path(image.filename or "image.png").suffix.lower() or ".png"
    if safe_ext not in {".jpg", ".jpeg", ".png"}:
        safe_ext = ".png"
    original_rel = f"originals/{uid}{safe_ext}"
    result_rel = f"results/{uid}.png"
    original_abs = settings.storage_dir / original_rel
    result_abs = settings.storage_dir / result_rel

    original_abs.parent.mkdir(parents=True, exist_ok=True)
    original_abs.write_bytes(raw)

    inspection = Inspection(
        operator_id=user.id,
        device_id=device.id if device else None,
        original_filename=image.filename or f"image{safe_ext}",
        original_path=original_rel,
        image_width=w,
        image_height=h,
        status=InspectionStatus.PENDING,
        conf_threshold=effective_conf,
        notes=notes,
        board_model=(board_model.strip() or None) if board_model else None,
        golden_board_profile_id=golden_board_profile_id,
        golden_alignment_used=gold_meta.applied if gold_meta else False,
        alignment_mae_before=gold_meta.mae_before if gold_meta else None,
        alignment_mae_after=gold_meta.mae_after if gold_meta else None,
    )
    db.add(inspection)
    db.flush()

    try:
        detector = get_detector()
        result = detector.predict(
            rgb,
            conf_threshold=effective_conf,
            iou_threshold=effective_iou,
        )
        defects = apply_component_tilt_rules(rgb, result.defects, db)
        defects = _apply_golden_region_checks(
            db,
            defects,
            golden_board_profile_id=golden_board_profile_id,
            gold_meta=gold_meta,
            frame_width=w,
            frame_height=h,
        )
        defects = _apply_golden_polarity_checks(
            db,
            defects,
            inspection_rgb=rgb,
            golden_board_profile_id=golden_board_profile_id,
            gold_meta=gold_meta,
            frame_width=w,
            frame_height=h,
        )

        rendered = render_result_image(rgb, defects)
        result_abs.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(result_abs), rendered, [cv2.IMWRITE_PNG_COMPRESSION, 3])

        for d in defects:
            db.add(
                Defect(
                    inspection_id=inspection.id,
                    class_code=d.class_code,
                    class_name=d.class_name,
                    confidence=d.confidence,
                    bbox_x1=d.x1,
                    bbox_y1=d.y1,
                    bbox_x2=d.x2,
                    bbox_y2=d.y2,
                )
            )

        sem_map = load_mappings(db)
        inspection.result_path = result_rel
        inspection.defects_count = sum(
            1 for d in defects if counts_as_protocol_defect(d.class_code, sem_map)
        )
        inspection.avg_confidence = (
            sum(d.confidence for d in defects) / len(defects)
            if defects
            else None
        )
        inspection.inference_time_ms = round(result.inference_time_ms, 2)
        inspection.status = InspectionStatus.SUCCESS

        write_audit(
            db,
            user=user,
            action="inspection_create",
            target=str(inspection.id),
            details=(
                f"defects={inspection.defects_count}, "
                f"backend={result.backend}, "
                f"tiling={getattr(result, 'used_tiling', False)}, "
                f"time_ms={inspection.inference_time_ms}, "
                f"conf={effective_conf:.2f}, "
                f"device={device.name if device else '-'}, "
                f"board_model={inspection.board_model or '-'}, "
                f"golden_profile={inspection.golden_board_profile_id or '-'}, "
                f"golden_align={inspection.golden_alignment_used}"
            ),
            request=request,
        )
    except Exception as exc:
        logger.exception("Ошибка при выполнении инспекции")
        inspection.status = InspectionStatus.FAILED
        inspection.error_message = f"{type(exc).__name__}: {exc}"
        write_audit(
            db,
            user=user,
            action="inspection_failed",
            target=str(inspection.id),
            details=inspection.error_message,
            request=request,
        )

    db.commit()
    db.refresh(inspection)
    inspection = db.execute(
        select(Inspection)
        .options(
            selectinload(Inspection.defects),
            selectinload(Inspection.operator),
            selectinload(Inspection.device),
        )
        .where(Inspection.id == inspection.id)
    ).scalar_one()
    return _to_detail(inspection, db)


# ---------------------------------------------------------------------------
# Живой анализ кадра (без сохранения в БД)
# ---------------------------------------------------------------------------
@router.post(
    "/live",
    response_model=LiveDetectionResult,
    summary="Детекция одного кадра live-потока",
)
async def live_detect(
    image: UploadFile = File(..., description="JPEG-кадр с камеры"),
    conf_threshold: float | None = Form(default=None, ge=0.0, le=1.0),
    golden_board_profile_id: int | None = Form(default=None),
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> LiveDetectionResult:
    """Быстрая детекция без записи в БД/на диск.

    Предназначено для клиентов, которые накладывают результат поверх
    видеопотока. Клиент сам решает, нужно ли сохранить кадр как полноценную
    инспекцию — для этого использует /api/inspections.
    """
    _resolve_golden_board_profile(
        db, golden_board_profile_id=golden_board_profile_id, user=user
    )
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой кадр")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Размер кадра превышает лимит",
        )

    import numpy as np

    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise HTTPException(status_code=400, detail="Не удалось декодировать кадр")
    # В live-режиме допускаем кадры меньшего размера (не применяем жёсткий ≥640×640
    # порог ТЗ, т.к. кадр намеренно уменьшен клиентом для скорости).
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = apply_detection_preprocess(rgb)
    rgb, gold_meta = _maybe_align_with_golden_profile(db, rgb, golden_board_profile_id)
    h, w = rgb.shape[:2]

    effective_conf = (
        conf_threshold
        if conf_threshold is not None
        else float(dynamic_settings.get(db, "detection_conf_threshold"))
    )
    effective_iou = float(dynamic_settings.get(db, "detection_iou_threshold"))

    detector = get_detector()
    result = detector.predict(
        rgb,
        conf_threshold=effective_conf,
        iou_threshold=effective_iou,
    )
    defects = apply_component_tilt_rules(rgb, result.defects, db)
    defects = _apply_golden_region_checks(
        db,
        defects,
        golden_board_profile_id=golden_board_profile_id,
        gold_meta=gold_meta,
        frame_width=w,
        frame_height=h,
    )
    defects = _apply_golden_polarity_checks(
        db,
        defects,
        inspection_rgb=rgb,
        golden_board_profile_id=golden_board_profile_id,
        gold_meta=gold_meta,
        frame_width=w,
        frame_height=h,
    )

    sem_map = load_mappings(db)
    semantic_defect_count = sum(
        1 for d in defects if counts_as_protocol_defect(d.class_code, sem_map)
    )
    detections_count = len(defects)

    return LiveDetectionResult(
        image_width=w,
        image_height=h,
        inference_time_ms=round(result.inference_time_ms, 2),
        backend=result.backend,
        conf_threshold=effective_conf,
        detections_count=detections_count,
        semantic_defect_count=semantic_defect_count,
        golden_board_profile_id=golden_board_profile_id,
        golden_alignment_used=gold_meta.applied if gold_meta else False,
        alignment_mae_before=gold_meta.mae_before if gold_meta else None,
        alignment_mae_after=gold_meta.mae_after if gold_meta else None,
        defects=[
            DefectOut(
                id=i,
                class_code=d.class_code,
                class_name=d.class_name,
                confidence=d.confidence,
                bbox_x1=d.x1,
                bbox_y1=d.y1,
                bbox_x2=d.x2,
                bbox_y2=d.y2,
                is_reviewed=False,
                is_real_defect=True,
                semantic_kind=semantic_kind_for_class(d.class_code, sem_map),
                exclude_from_training=False,
            )
            for i, d in enumerate(defects)
        ],
    )


# ---------------------------------------------------------------------------
# Журнал (Ф8) с фильтрами
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=list[InspectionListItem],
    summary="Журнал инспекций (Ф8)",
)
def list_inspections(
    from_date: datetime | None = Query(default=None, description="Начальная дата"),
    to_date: datetime | None = Query(default=None, description="Конечная дата"),
    operator_id: int | None = Query(default=None),
    device_id: int | None = Query(default=None),
    class_code: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> list[InspectionListItem]:
    stmt = (
        select(Inspection)
        .options(
            selectinload(Inspection.operator),
            selectinload(Inspection.device),
        )
        .order_by(Inspection.created_at.desc())
    )
    if not has_backoffice_role(user):
        stmt = stmt.where(Inspection.operator_id == user.id)
    else:
        if operator_id is not None:
            stmt = stmt.where(Inspection.operator_id == operator_id)
    if device_id is not None:
        stmt = stmt.where(Inspection.device_id == device_id)
    if from_date:
        stmt = stmt.where(Inspection.created_at >= from_date)
    if to_date:
        stmt = stmt.where(Inspection.created_at <= to_date)
    if class_code:
        stmt = stmt.join(Inspection.defects).where(Defect.class_code == class_code).distinct()

    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().unique().all()
    return [
        InspectionListItem(
            id=i.id,
            operator_id=i.operator_id,
            operator_username=i.operator.username if i.operator else None,
            device_id=i.device_id,
            device_name=i.device.name if i.device else None,
            original_filename=i.original_filename,
            board_model=i.board_model,
            golden_board_profile_id=i.golden_board_profile_id,
            golden_alignment_used=i.golden_alignment_used,
            status=i.status,
            defects_count=i.defects_count,
            avg_confidence=i.avg_confidence,
            inference_time_ms=i.inference_time_ms,
            created_at=i.created_at,
        )
        for i in rows
    ]


# ---------------------------------------------------------------------------
# Массовая очистка: только администратор
# ---------------------------------------------------------------------------
@router.post(
    "/admin/purge-all",
    response_model=PurgeAllInspectionsOut,
    summary="Удалить все инспекции и связанные файлы (глобально, администратор)",
)
def purge_all_inspections(
    request: Request,
    payload: PurgeAllInspectionsIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> PurgeAllInspectionsOut:
    if payload.confirm.strip() != CONFIRM_PURGE_ALL_INSPECTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Введите в точности: {CONFIRM_PURGE_ALL_INSPECTIONS}',
        )
    rows = db.execute(select(Inspection)).scalars().all()
    n = len(rows)
    for insp in rows:
        _remove_inspection_artifacts(insp)
    db.execute(delete(Inspection))
    write_audit(
        db,
        user=admin,
        action="inspection_purge_all",
        target="all",
        details=f"deleted={n}",
        request=request,
    )
    db.commit()
    return PurgeAllInspectionsOut(deleted=n)


# ---------------------------------------------------------------------------
# Детальная информация
# ---------------------------------------------------------------------------
@router.get(
    "/{inspection_id}",
    response_model=InspectionDetailOut,
    summary="Детали инспекции",
)
def get_inspection(
    inspection_id: int,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> InspectionDetailOut:
    inspection = db.execute(
        select(Inspection)
        .options(
            selectinload(Inspection.defects),
            selectinload(Inspection.operator),
            selectinload(Inspection.device),
        )
        .where(Inspection.id == inspection_id)
    ).scalar_one_or_none()
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")
    _check_access(inspection, user)
    return _to_detail(inspection, db)


# ---------------------------------------------------------------------------
# Изображения (оригинал/результат)
# ---------------------------------------------------------------------------
@router.get(
    "/{inspection_id}/image",
    summary="Получение изображения (оригинал или результат)",
)
def get_image(
    inspection_id: int,
    kind: str = Query(default="result", pattern="^(original|result)$"),
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> Response:
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")
    _check_access(inspection, user)

    rel = inspection.original_path if kind == "original" else inspection.result_path
    if not rel:
        raise HTTPException(status_code=404, detail="Изображение отсутствует")
    abs_path = settings.storage_dir / rel
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    ext = abs_path.suffix.lower()
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(
        ext.lstrip("."), "application/octet-stream"
    )
    return Response(content=abs_path.read_bytes(), media_type=media_type)


# ---------------------------------------------------------------------------
# Кроп отдельного дефекта (для ручной проверки оператором)
# ---------------------------------------------------------------------------
@router.get(
    "/{inspection_id}/defects/{defect_id}/crop",
    summary="Кроп области одного дефекта из исходного изображения",
)
def get_defect_crop(
    inspection_id: int,
    defect_id: int,
    padding: int = Query(default=24, ge=0, le=400),
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> Response:
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")
    _check_access(inspection, user)
    defect = db.get(Defect, defect_id)
    if defect is None or defect.inspection_id != inspection_id:
        raise HTTPException(status_code=404, detail="Дефект не найден")
    if not inspection.original_path:
        raise HTTPException(status_code=404, detail="Нет исходного изображения")
    abs_path = settings.storage_dir / inspection.original_path
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")

    bgr = cv2.imread(str(abs_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise HTTPException(status_code=500, detail="Не удалось прочитать изображение")
    h, w = bgr.shape[:2]
    x1 = max(0, defect.bbox_x1 - padding)
    y1 = max(0, defect.bbox_y1 - padding)
    x2 = min(w, defect.bbox_x2 + padding)
    y2 = min(h, defect.bbox_y2 + padding)
    if x2 <= x1 or y2 <= y1:
        raise HTTPException(status_code=400, detail="Некорректные координаты дефекта")
    crop = bgr[y1:y2, x1:x2]
    ok, buf = cv2.imencode(".png", crop, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise HTTPException(status_code=500, detail="Не удалось закодировать кроп")
    return Response(content=buf.tobytes(), media_type="image/png")


# ---------------------------------------------------------------------------
# Ручная проверка оператором (отметка «брак»/«не брак» для каждого дефекта)
# ---------------------------------------------------------------------------
def _render_reviewed_result(
    original_rgb: np.ndarray, defects: list[Defect]
) -> np.ndarray:
    """Рендерит PNG результата после проверки: полный кадр, рамки только у подтверждённых браков."""
    det = [
        DetectedDefect(
            class_code=d.class_code,
            class_name=d.class_name,
            confidence=d.confidence,
            x1=d.bbox_x1,
            y1=d.bbox_y1,
            x2=d.bbox_x2,
            y2=d.bbox_y2,
        )
        for d in defects
        if d.is_real_defect
    ]
    return render_result_image(original_rgb, det)


def _export_training_artifacts(
    inspection: Inspection,
    original_rgb: np.ndarray,
    *,
    db: Session,
) -> Path:
    """Сохраняет на диск набор файлов для дообучения модели.

    Отклонённые оператором дефекты («не брак») **не попадают** в обучающую
    выборку: они не указываются в ``labels.txt`` и в JSON-файле ``dataset.yaml``
    помечаются отдельным списком ``false_positives``. Их кропы сохраняются в
    отдельную папку ``false_positives/`` — их можно использовать как «hard
    negatives», но они никогда не будут восприняты пайплайном обучения как
    пример дефекта соответствующего класса.

    Структура каталога (внутри ``storage/training/<inspection_id>/``)::

        original.<ext>                — исходное изображение без закраски
        masked.png                    — то же, но закрашено всё, кроме подтверждённых
                                        дефектов (итоговый протокол)
        labels.txt                    — YOLO-разметка по правилам ниже (не ошибочные отклонённые компоненты)
        defects/<i>_<class>.png       — кропы подтверждённых дефектов
        false_positives/<i>_<class>.png — кропы ложных срабатываний модели
        rejected_predictions/        — кропы отклонённых срабатываний с семантикой «компонент»/«игнор»
        README.txt                    — пояснение для человека, читающего каталог

    Если после проверки у инспекции не осталось реальных дефектов —
    ``labels.txt`` остаётся пустым (это валидный YOLO-формат, означающий
    «на изображении ничего нет»), а ``masked.png`` превращается в полностью
    чёрную картинку.
    """
    rel_dir = f"training/{inspection.id}"
    abs_dir = settings.storage_dir / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    defects_dir = abs_dir / "defects"
    fp_dir = abs_dir / "false_positives"
    context_dir = abs_dir / "context_objects"
    rejected_dir = abs_dir / "rejected_predictions"
    # На случай повторной проверки — чистим, чтобы устаревшие кропы не остались.
    for sub in (defects_dir, fp_dir, context_dir, rejected_dir):
        if sub.exists():
            for p in sub.glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
        sub.mkdir(parents=True, exist_ok=True)

    # 1) Оригинал (без закраски).
    src_abs = settings.storage_dir / inspection.original_path
    original_ext = src_abs.suffix.lower() or ".png"
    original_dst = abs_dir / f"original{original_ext}"
    try:
        original_dst.write_bytes(src_abs.read_bytes())
    except OSError:
        cv2.imwrite(
            str(abs_dir / "original.png"),
            cv2.cvtColor(original_rgb, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        original_dst = abs_dir / "original.png"

    # 2) «masked» по ТЗ 4.8.4 — чёрный фон, видны только области подтверждённых дефектов.
    masked_dst = abs_dir / "masked.png"
    real_dets = [
        DetectedDefect(
            class_code=d.class_code,
            class_name=d.class_name,
            confidence=d.confidence,
            x1=d.bbox_x1,
            y1=d.bbox_y1,
            x2=d.bbox_x2,
            y2=d.bbox_y2,
        )
        for d in inspection.defects
        if d.is_real_defect
    ]
    masked_bgr = render_masked_defect_protocol(original_rgb, real_dets)
    cv2.imwrite(str(masked_dst), masked_bgr, [cv2.IMWRITE_PNG_COMPRESSION, 3])

    # 3) Отдельные кропы:
    #   * defects/ — подтверждённые дефекты;
    #   * false_positives/ — отклонённые дефектные классы (hard negatives);
    #   * rejected_predictions/ — отклонённые «компонент»/«игнор» (ошибочный класс, не в labels.txt).

    h, w = original_rgb.shape[:2]
    bgr_full = cv2.cvtColor(original_rgb, cv2.COLOR_RGB2BGR)
    defects_payload: list[dict] = []
    false_positives_payload: list[dict] = []
    context_objects_payload: list[dict] = []
    rejected_predictions_payload: list[dict] = []

    defect_codes = {
        str(c.get("code"))
        for c in DEFECT_CLASSES
        if bool(c.get("is_defect", True))
    }

    sem_map = load_mappings(db)

    for idx, d in enumerate(inspection.defects, start=1):
        pad = 24
        x1 = max(0, d.bbox_x1 - pad)
        y1 = max(0, d.bbox_y1 - pad)
        x2 = min(w, d.bbox_x2 + pad)
        y2 = min(h, d.bbox_y2 + pad)
        if x2 <= x1 or y2 <= y1:
            continue
        sk = semantic_kind_for_class(d.class_code, sem_map)
        excl = bool(getattr(d, "exclude_from_training", False))
        if d.is_real_defect:
            target_dir = defects_dir
            sub_name = "defects"
        elif (
            not d.is_real_defect
            and sk in ("component", "ignore")
            and excl
        ):
            target_dir = rejected_dir
            sub_name = "rejected_predictions"
        elif d.class_code in defect_codes:
            target_dir = fp_dir
            sub_name = "false_positives"
        elif sk in ("component", "ignore"):
            target_dir = context_dir
            sub_name = "context_objects"
        else:
            target_dir = context_dir
            sub_name = "context_objects"
        name = f"{idx:02d}_{d.class_code}.png"
        crop_path = target_dir / name
        cv2.imwrite(
            str(crop_path),
            bgr_full[y1:y2, x1:x2],
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        entry = {
            "index": idx,
            "file": f"{sub_name}/{name}",
            "class_code": d.class_code,
            "class_name": d.class_name,
            "confidence": round(float(d.confidence), 4),
            "bbox": [d.bbox_x1, d.bbox_y1, d.bbox_x2, d.bbox_y2],
            "is_real_defect": bool(d.is_real_defect),
            "is_reviewed": bool(d.is_reviewed),
            "exclude_from_training": excl,
        }
        if d.is_real_defect:
            defects_payload.append(entry)
        elif not d.is_real_defect and sk in ("component", "ignore") and excl:
            rejected_predictions_payload.append(entry)
        elif d.class_code in defect_codes:
            false_positives_payload.append(entry)
        elif not d.is_real_defect and sk in ("component", "ignore"):
            context_objects_payload.append(entry)
        else:
            context_objects_payload.append(entry)

    # 4) Разметка YOLO:
    #   - подтверждённые объекты (is_real_defect);
    #   - отклонённые дефектные классы — не включаем;
    #   - компонент/игнор «не брак» с exclude_from_training — не включаем (ошибочный класс);
    #   - компонент/игнор «не брак» без exclude — включаем (верная детекция для дообучения);
    #   - прочий контекст — включаем.
    yolo_lines: list[str] = []
    runtime_classes = get_detector().get_defect_classes()
    class_index = {c["code"]: i for i, c in enumerate(runtime_classes)}
    for d in inspection.defects:
        if not d.is_real_defect:
            if d.class_code in defect_codes:
                continue
            sk = semantic_kind_for_class(d.class_code, sem_map)
            if sk in ("component", "ignore") and getattr(
                d, "exclude_from_training", False
            ):
                continue
        cx = ((d.bbox_x1 + d.bbox_x2) / 2.0) / w
        cy = ((d.bbox_y1 + d.bbox_y2) / 2.0) / h
        bw = (d.bbox_x2 - d.bbox_x1) / w
        bh = (d.bbox_y2 - d.bbox_y1) / h
        cls_idx = class_index.get(d.class_code)
        if cls_idx is None:
            cls_idx = len(class_index)
            class_index[d.class_code] = cls_idx
        yolo_lines.append(f"{cls_idx} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    # Пустой labels.txt — валидная YOLO-разметка «на этом изображении
    # ничего нет», именно то, что нужно после того, как оператор забраковал
    # все найденные моделью срабатывания.
    (abs_dir / "labels.txt").write_text(
        "\n".join(yolo_lines) + ("\n" if yolo_lines else ""),
        encoding="utf-8",
    )

    annotations = {
        "inspection_id": inspection.id,
        "created_at": inspection.created_at.isoformat(),
        "reviewed_at": (
            inspection.reviewed_at.isoformat() if inspection.reviewed_at else None
        ),
        "image_width": w,
        "image_height": h,
        "conf_threshold": inspection.conf_threshold,
        "class_names": [c["code"] for c in runtime_classes] + [
            code for code, idx in sorted(class_index.items(), key=lambda x: x[1])
            if code not in {c["code"] for c in runtime_classes}
        ],
        "original": original_dst.name,
        "masked": masked_dst.name,
        "labels_file": "labels.txt",
        "defects": defects_payload,
        "false_positives": false_positives_payload,
        "context_objects": context_objects_payload,
        "rejected_predictions": rejected_predictions_payload,
        "summary": {
            "model_predictions": len(inspection.defects),
            "confirmed": len(defects_payload),
            "rejected": len(false_positives_payload),
            "context_objects": len(context_objects_payload),
            "rejected_predictions": len(rejected_predictions_payload),
            "is_clean_after_review": len(defects_payload) == 0,
        },
    }
    (abs_dir / "annotations.json").write_text(
        json.dumps(annotations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    readme_lines = [
        f"Инспекция №{inspection.id}, создана {inspection.created_at.isoformat()}.",
        f"Проверена оператором: {inspection.reviewed_at.isoformat() if inspection.reviewed_at else '—'}.",
        "",
        "Файлы для дообучения модели:",
        f"  original{original_ext}   — исходная фотография без закраски.",
        "  masked.png    — итоговый протокол: закрашено всё, кроме подтверждённых дефектов.",
        "  labels.txt    — YOLO-detect разметка: подтверждённые + контекст; без отклонённых компонентов.",
        "  defects/      — кропы ПОДТВЕРЖДЁННЫХ дефектов (обучающие примеры).",
        "  false_positives/ — отклонённые дефектные классы (hard negatives).",
        "  rejected_predictions/ — только при флаге «ошибочный класс» (не в labels.txt).",
        "  context_objects/ — прочие объекты «не брак», остающиеся позитивами в labels.txt.",
        "",
        f"Модель обнаружила: {len(inspection.defects)}",
        f"Подтверждено оператором: {len(defects_payload)}",
        f"Отклонено (дефектный класс / FP): {len(false_positives_payload)}",
        f"Отклонено (компонент/игнор — ошибочный класс): {len(rejected_predictions_payload)}",
        f"Недефектный контекст в labels.txt: {len(context_objects_payload)}",
    ]
    (abs_dir / "README.txt").write_text("\n".join(readme_lines), encoding="utf-8")

    return abs_dir


@router.post(
    "/{inspection_id}/review",
    response_model=InspectionDetailOut,
    summary="Ручная проверка дефектов оператором",
)
def review_inspection(
    inspection_id: int,
    payload: InspectionReviewIn,
    request: Request,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> InspectionDetailOut:
    """Принимает оценки оператора («брак»/«не брак») по каждому дефекту.

    * Обновляет флаги ``is_reviewed`` и ``is_real_defect`` у каждого дефекта.
    * Перерисовывает итоговое изображение: области, отмеченные как «не брак»,
      закрашиваются чёрным наравне с «фоном» платы. Подтверждённые дефекты
      остаются видимыми и обведены цветной рамкой.
    * Сохраняет на диск артефакты для дообучения модели
      (``storage/training/<id>/``).
    * Пересчитывает сводные метрики инспекции (``defects_count``,
      ``avg_confidence``).

    Повторный вызов допустим: артефакты перезаписываются.
    """
    inspection = _load_inspection_full(db, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")
    _check_access(inspection, user)
    if inspection.status != InspectionStatus.SUCCESS:
        raise HTTPException(
            status_code=400,
            detail="Проверка доступна только для успешных инспекций",
        )

    # Построим быстрый индекс по переданным оценкам и применим их.
    review_by_id = {r.defect_id: r for r in payload.reviews}

    known_ids = {d.id for d in inspection.defects}
    unknown = set(review_by_id.keys()) - known_ids
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестные идентификаторы дефектов: {sorted(unknown)}",
        )

    sem_map = load_mappings(db)

    for d in inspection.defects:
        if d.id in review_by_id:
            r = review_by_id[d.id]
            d.is_real_defect = bool(r.is_real_defect)
            # Ошибочный класс имеет смысл только при «не брак».
            d.exclude_from_training = bool(r.exclude_from_training) and not d.is_real_defect
            d.is_reviewed = True

    for d in inspection.defects:
        if d.is_reviewed:
            continue
        av = auto_real_defect_if_unreviewed(d.class_code, sem_map)
        if av is not None:
            d.is_real_defect = av
            d.exclude_from_training = False
            d.is_reviewed = True

    still = [d.id for d in inspection.defects if not d.is_reviewed]
    if still:
        raise HTTPException(
            status_code=400,
            detail=(
                "Для части дефектов не передана оценка. Укажите вердикт по id: "
                f"{sorted(still)}"
            ),
        )

    # Читаем исходник, перерисовываем результат на основе подтверждённых дефектов.
    if not inspection.original_path:
        raise HTTPException(status_code=500, detail="Нет исходного изображения")
    src_abs = settings.storage_dir / inspection.original_path
    if not src_abs.exists():
        raise HTTPException(status_code=500, detail="Исходный файл не найден")
    bgr_orig = cv2.imread(str(src_abs), cv2.IMREAD_COLOR)
    if bgr_orig is None:
        raise HTTPException(status_code=500, detail="Не удалось прочитать исходник")
    rgb_orig = cv2.cvtColor(bgr_orig, cv2.COLOR_BGR2RGB)

    rendered = _render_reviewed_result(rgb_orig, list(inspection.defects))

    # Перезаписываем result-файл (имя из БД не меняем).
    if not inspection.result_path:
        inspection.result_path = f"results/{uuid.uuid4().hex}.png"
    result_abs = settings.storage_dir / inspection.result_path
    result_abs.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(result_abs), rendered, [cv2.IMWRITE_PNG_COMPRESSION, 3])

    # Перерасчёт сводных показателей: теперь считаем только подтверждённые дефекты.
    real_defects = [d for d in inspection.defects if d.is_real_defect]
    inspection.defects_count = len(real_defects)
    inspection.avg_confidence = (
        sum(d.confidence for d in real_defects) / len(real_defects)
        if real_defects
        else None
    )
    inspection.reviewed_at = datetime.utcnow()

    # Экспорт артефактов для дообучения модели.
    try:
        training_dir = _export_training_artifacts(inspection, rgb_orig, db=db)
        inspection.training_dir = str(
            training_dir.relative_to(settings.storage_dir)
        ).replace("\\", "/")
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сохранить артефакты для дообучения")

    accepted = sum(1 for d in inspection.defects if d.is_real_defect)
    rejected = sum(1 for d in inspection.defects if not d.is_real_defect)
    write_audit(
        db,
        user=user,
        action="inspection_review",
        target=str(inspection.id),
        details=f"accepted={accepted}, rejected={rejected}",
        request=request,
    )

    db.commit()
    db.refresh(inspection)
    inspection = _load_inspection_full(db, inspection_id)
    return _to_detail(inspection, db)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Экспорт отчётов (Ф10)
# ---------------------------------------------------------------------------
@router.get(
    "/{inspection_id}/export/pdf",
    summary="Экспорт протокола в PDF (Ф10)",
)
def export_pdf(
    inspection_id: int,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> Response:
    inspection = db.execute(
        select(Inspection)
        .options(
            selectinload(Inspection.defects),
            selectinload(Inspection.operator),
            selectinload(Inspection.device),
        )
        .where(Inspection.id == inspection_id)
    ).scalar_one_or_none()
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")
    _check_access(inspection, user)

    data = generate_pdf_report(inspection)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="inspection_{inspection.id}.pdf"'
        },
    )


@router.get(
    "/{inspection_id}/export/csv",
    summary="Экспорт протокола в CSV (Ф10)",
)
def export_csv(
    inspection_id: int,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> Response:
    inspection = db.execute(
        select(Inspection)
        .options(
            selectinload(Inspection.defects),
            selectinload(Inspection.operator),
            selectinload(Inspection.device),
        )
        .where(Inspection.id == inspection_id)
    ).scalar_one_or_none()
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")
    _check_access(inspection, user)

    data = generate_csv_report(inspection)
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="inspection_{inspection.id}.csv"'
        },
    )


@router.get(
    "/{inspection_id}/export/training.zip",
    summary="Скачать архив для дообучения (фото+разметка)",
)
def export_training_zip(
    inspection_id: int,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> Response:
    inspection = _load_inspection_full(db, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")
    _check_access(inspection, user)
    if not inspection.training_dir:
        raise HTTPException(
            status_code=400,
            detail="Артефакты дообучения ещё не сформированы. Сначала выполните ручную проверку.",
        )
    abs_dir = settings.storage_dir / inspection.training_dir
    if not abs_dir.exists():
        raise HTTPException(status_code=404, detail="Каталог артефактов не найден на диске")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in abs_dir.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=f"inspection_{inspection.id}/{p.relative_to(abs_dir)}")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="inspection_{inspection.id}_training.zip"'
        },
    )


# ---------------------------------------------------------------------------
# Удаление (руководитель)
# ---------------------------------------------------------------------------
@router.delete(
    "/{inspection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удаление инспекции (руководитель)",
)
def delete_inspection(
    inspection_id: int,
    request: Request,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> Response:
    inspection = db.get(Inspection, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Инспекция не найдена")

    _remove_inspection_artifacts(inspection)

    db.delete(inspection)
    write_audit(
        db,
        user=manager,
        action="inspection_delete",
        target=str(inspection_id),
        request=request,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Справочные функции
# ---------------------------------------------------------------------------
@router.get(
    "/recent/count",
    summary="Быстрая метрика количества инспекций за 24 часа (для виджетов)",
)
def recent_count(
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(hours=24)
    stmt = select(func.count()).select_from(Inspection).where(Inspection.created_at >= since)
    if not has_backoffice_role(user):
        stmt = stmt.where(Inspection.operator_id == user.id)
    return {"count_24h": int(db.execute(stmt).scalar_one() or 0)}
