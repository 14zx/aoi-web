"""CRUD эталонных профилей плат (Golden Board Manager, ТЗ п. 5).

Просмотр, создание и редактирование — администратор или руководитель.
Удаление профиля — только администратор.
Оператор при инспекции выбирает эталон из закреплённых за ним (``/choices``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import cv2
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import GoldenBoardProfile, User, UserRole
from ..schemas.golden_board import (
    GoldenBoardChoiceOut,
    GoldenBoardCreate,
    GoldenBoardDetailOut,
    GoldenBoardMarkupIn,
    GoldenBoardOut,
    GoldenBoardUpdate,
)
from ..services.golden_alignment import extract_reference_image_rel
from ..services.golden_auto_markup import auto_markup_regions_from_rgb
from ..services.preprocessing import ImageValidationError, load_image
from .deps import require_admin, require_any, require_manager, write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/golden-boards", tags=["Golden Board"])


def _payload_to_json(payload: dict | list) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"payload не сериализуется в JSON: {exc}",
        ) from exc


def _parse_payload(raw: str) -> dict | list:
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("golden_board id payload corrupt: %s", exc)
        raise HTTPException(status_code=500, detail="Повреждённый JSON эталона") from exc
    if not isinstance(data, (dict, list)):
        raise HTTPException(status_code=500, detail="Эталон JSON должен быть объектом или массивом")
    return data


def _as_dict(payload: dict | list) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    return {"legacy_payload": payload, "regions": []}


def _detail_out(row: GoldenBoardProfile) -> GoldenBoardDetailOut:
    payload = _parse_payload(row.payload_json)
    rel = extract_reference_image_rel(payload)
    ref_url = f"/api/golden-boards/{row.id}/reference-image" if rel else None
    base = _to_out(row, has_reference=rel is not None)
    return GoldenBoardDetailOut(
        **base.model_dump(),
        payload=payload,
        reference_image_url=ref_url,
    )


def _reference_rel_for_profile(profile_id: int) -> str:
    return f"golden_boards/{profile_id}/reference.png"


def _validate_designated_operator(db: Session, operator_id: int | None) -> None:
    if operator_id is None:
        return
    op = db.get(User, operator_id)
    if op is None or op.role != UserRole.OPERATOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Закрепить эталон можно только за сотрудником (оператором)",
        )


def _to_out(row: GoldenBoardProfile, *, has_reference: bool) -> GoldenBoardOut:
    return GoldenBoardOut(
        id=row.id,
        name=row.name,
        board_model=row.board_model,
        author_id=row.author_id,
        designated_operator_id=row.designated_operator_id,
        designated_operator_username=(
            row.designated_operator.username if row.designated_operator else None
        ),
        created_at=row.created_at,
        has_reference_image=has_reference,
    )


def _load(db: Session, profile_id: int) -> GoldenBoardProfile:
    row = db.execute(
        select(GoldenBoardProfile)
        .where(GoldenBoardProfile.id == profile_id)
        .options(selectinload(GoldenBoardProfile.designated_operator))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return row


@router.get("", response_model=list[GoldenBoardOut])
def list_golden_boards(
    db: Session = Depends(get_db),
    _: User = Depends(require_manager),
) -> list[GoldenBoardOut]:
    rows = db.scalars(
        select(GoldenBoardProfile)
        .options(selectinload(GoldenBoardProfile.designated_operator))
        .order_by(GoldenBoardProfile.created_at.desc())
    ).all()
    out: list[GoldenBoardOut] = []
    for row in rows:
        payload = _parse_payload(row.payload_json)
        rel = extract_reference_image_rel(payload)
        out.append(_to_out(row, has_reference=rel is not None))
    return out


@router.get("/choices", response_model=list[GoldenBoardChoiceOut], summary="Список эталонов для выбора при инспекции")
def list_golden_board_choices(
    db: Session = Depends(get_db),
    user: User = Depends(require_any),
) -> list[GoldenBoardChoiceOut]:
    stmt = select(GoldenBoardProfile).order_by(GoldenBoardProfile.name)
    if user.role == UserRole.OPERATOR:
        stmt = stmt.where(GoldenBoardProfile.designated_operator_id == user.id)
    rows = db.scalars(stmt).all()
    return [
        GoldenBoardChoiceOut(id=row.id, name=row.name, board_model=row.board_model)
        for row in rows
    ]


@router.post("", response_model=GoldenBoardDetailOut, status_code=status.HTTP_201_CREATED)
def create_golden_board(
    body: GoldenBoardCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> GoldenBoardDetailOut:
    row = GoldenBoardProfile(
        name=body.name.strip(),
        board_model=(body.board_model.strip() if body.board_model else None),
        payload_json=_payload_to_json(body.payload),
        author_id=user.id,
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        user=user,
        action="golden_board_create",
        target=str(row.id),
        details=row.name,
    )
    db.commit()
    db.refresh(row)
    return _detail_out(row)


@router.get("/{profile_id}", response_model=GoldenBoardDetailOut)
def get_golden_board(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_manager),
) -> GoldenBoardDetailOut:
    return _detail_out(_load(db, profile_id))


@router.patch(
    "/{profile_id}",
    response_model=GoldenBoardOut,
    summary="Закрепление эталона за сотрудником (руководитель)",
)
def update_golden_board(
    profile_id: int,
    payload: GoldenBoardUpdate,
    request: Request,
    db: Session = Depends(get_db),
    manager: User = Depends(require_manager),
) -> GoldenBoardOut:
    row = _load(db, profile_id)
    changes: list[str] = []
    if "designated_operator_id" in payload.model_fields_set:
        new_op = payload.designated_operator_id
        _validate_designated_operator(db, new_op)
        if row.designated_operator_id != new_op:
            row.designated_operator_id = new_op
            changes.append(f"designated_operator_id={new_op}")
    if changes:
        write_audit(
            db,
            user=manager,
            action="golden_board_update",
            target=str(profile_id),
            details=", ".join(changes),
            request=request,
        )
    db.commit()
    row = _load(db, profile_id)
    payload_data = _parse_payload(row.payload_json)
    rel = extract_reference_image_rel(payload_data)
    return _to_out(row, has_reference=rel is not None)


@router.get("/{profile_id}/reference-image")
def get_reference_image(
    profile_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_manager),
) -> Response:
    row = db.get(GoldenBoardProfile, profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    payload = _parse_payload(row.payload_json)
    rel = extract_reference_image_rel(payload)
    if not rel:
        raise HTTPException(status_code=404, detail="Опорный снимок не загружен")
    from ..config import settings

    path = settings.storage_dir / rel.replace("\\", "/")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл эталона не найден на диске")
    ext = path.suffix.lower()
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(
        ext.lstrip("."), "application/octet-stream"
    )
    return Response(content=path.read_bytes(), media_type=media_type)


@router.post("/{profile_id}/reference-image", response_model=GoldenBoardDetailOut)
async def upload_reference_image(
    profile_id: int,
    image: UploadFile = File(..., description="JPEG/PNG опорного кадра (≥640×640)"),
    auto_markup: bool = Form(default=True, description="Авторазметка YOLO после загрузки"),
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> GoldenBoardDetailOut:
    row = db.get(GoldenBoardProfile, profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if image.content_type and image.content_type not in {"image/jpeg", "image/png", "image/jpg"}:
        raise HTTPException(status_code=415, detail="Поддерживаются только JPEG и PNG")
    try:
        rgb = load_image(raw)
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from ..config import settings

    rel = _reference_rel_for_profile(profile_id)
    abs_path = settings.storage_dir / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if not cv2.imwrite(str(abs_path), bgr, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise HTTPException(status_code=500, detail="Не удалось сохранить снимок")

    merged = _as_dict(_parse_payload(row.payload_json))
    merged["reference_image_rel"] = rel
    if auto_markup:
        try:
            merged["regions"] = auto_markup_regions_from_rgb(rgb, db)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Golden auto-markup failed profile_id=%s", profile_id)
            raise HTTPException(
                status_code=500,
                detail=f"Снимок сохранён, но авторазметка не удалась: {type(exc).__name__}: {exc}",
            ) from exc
    row.payload_json = _payload_to_json(merged)
    write_audit(
        db,
        user=user,
        action="golden_board_reference_upload",
        target=str(profile_id),
        details=f"{rel}, auto_markup={auto_markup}, regions={len(merged.get('regions') or [])}",
    )
    db.commit()
    db.refresh(row)
    return _detail_out(row)


@router.post("/{profile_id}/auto-markup", response_model=GoldenBoardDetailOut)
def run_auto_markup(
    profile_id: int,
    replace: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> GoldenBoardDetailOut:
    """Запускает YOLO на опорном снимке и формирует ``regions`` с классами модели."""
    row = db.get(GoldenBoardProfile, profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    payload = _as_dict(_parse_payload(row.payload_json))
    rel = extract_reference_image_rel(payload)
    if not rel:
        raise HTTPException(status_code=400, detail="Сначала загрузите опорный снимок")
    from ..config import settings

    path = settings.storage_dir / rel.replace("\\", "/")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл эталона не найден на диске")
    try:
        rgb = load_image(path)
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_regions = auto_markup_regions_from_rgb(rgb, db)
    if replace:
        payload["regions"] = new_regions
    else:
        existing = payload.get("regions")
        if not isinstance(existing, list):
            existing = []
        payload["regions"] = list(existing) + new_regions

    row.payload_json = _payload_to_json(payload)
    write_audit(
        db,
        user=user,
        action="golden_board_auto_markup",
        target=str(profile_id),
        details=f"regions={len(payload.get('regions') or [])}, replace={replace}",
    )
    db.commit()
    db.refresh(row)
    return _detail_out(row)


@router.put("/{profile_id}/markup", response_model=GoldenBoardDetailOut)
def save_markup(
    profile_id: int,
    body: GoldenBoardMarkupIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> GoldenBoardDetailOut:
    row = db.get(GoldenBoardProfile, profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    merged = _as_dict(_parse_payload(row.payload_json))
    merged["regions"] = [r.model_dump() for r in body.regions]
    if body.region_tolerance_px is not None:
        merged["region_tolerance_px"] = body.region_tolerance_px
    row.payload_json = _payload_to_json(merged)
    write_audit(
        db,
        user=user,
        action="golden_board_markup_save",
        target=str(profile_id),
        details=f"regions={len(body.regions)}, tolerance={merged.get('region_tolerance_px')}",
    )
    db.commit()
    db.refresh(row)
    return _detail_out(row)


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_golden_board(
    profile_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Response:
    row = db.get(GoldenBoardProfile, profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    write_audit(db, user=user, action="golden_board_delete", target=str(profile_id), details=row.name)
    db.execute(delete(GoldenBoardProfile).where(GoldenBoardProfile.id == profile_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
