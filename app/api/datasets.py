"""Маршруты управления датасетами (весами модели детекции).

Только руководитель может загружать/удалять/активировать датасет.
Активный датасет используется детектором немедленно (hot-reload).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import BASE_DIR
from ..database import get_db
from ..models import Dataset, User
from ..schemas import DatasetOut
from ..services import dataset_manager
from .deps import require_any, require_manager, write_audit


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/datasets", tags=["Датасеты"])

# Разумный лимит для загрузки (.pt YOLO обычно 6–200 МБ).
MAX_DATASET_BYTES = 1024 * 1024 * 1024  # 1 ГБ
ALLOWED_EXTENSIONS = {".pt", ".pth", ".onnx", ".engine"}


def _to_out(ds: Dataset) -> DatasetOut:
    return DatasetOut(
        id=ds.id,
        name=ds.name,
        description=ds.description,
        file_size=ds.file_size,
        original_filename=ds.original_filename,
        is_active=ds.is_active,
        uploaded_by_id=ds.uploaded_by_id,
        uploaded_by_username=(ds.uploaded_by.username if ds.uploaded_by else None),
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )


@router.get("", response_model=list[DatasetOut], summary="Список датасетов")
def list_datasets(
    _: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> list[DatasetOut]:
    rows = (
        db.execute(
            select(Dataset)
            .options(selectinload(Dataset.uploaded_by))
            .order_by(Dataset.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_out(ds) for ds in rows]


@router.post(
    "",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузка нового датасета (веса модели)",
)
async def upload_dataset(
    request: Request,
    file: UploadFile = File(..., description="Файл весов модели (.pt/.pth/.onnx/.engine)"),
    name: str = Form(..., min_length=1, max_length=128),
    description: str | None = Form(default=None, max_length=2000),
    activate: bool = Form(default=False, description="Сразу сделать основным"),
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> DatasetOut:
    # Проверки
    existing = db.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Датасет с таким именем уже существует")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимое расширение файла. Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Создаём запись, чтобы получить id, затем сохраняем файл в директорию id.
    dataset = Dataset(
        name=name,
        description=description,
        file_path="",
        file_size=0,
        original_filename=file.filename,
        uploaded_by_id=manager.id,
        is_active=False,
    )
    db.add(dataset)
    db.flush()  # получить dataset.id без коммита

    try:
        dest_path, size = dataset_manager.store_weights_file(
            dataset.id, file.file, file.filename or "weights.pt", MAX_DATASET_BYTES
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        db.rollback()
        logger.exception("Ошибка сохранения файла датасета")
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить файл: {exc}")
    finally:
        await file.close()

    # Храним путь относительно BASE_DIR — чтобы переносить БД между машинами.
    try:
        rel = dest_path.relative_to(BASE_DIR)
    except ValueError:
        rel = dest_path
    dataset.file_path = str(rel).replace("\\", "/")
    dataset.file_size = size

    write_audit(
        db,
        user=manager,
        action="dataset_upload",
        target=name,
        details=f"{size} байт",
        request=request,
    )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        dataset_manager.remove_files(dataset)
        raise HTTPException(status_code=409, detail="Конфликт уникальности имени датасета")

    db.refresh(dataset)

    if activate:
        dataset_manager.activate(db, dataset)
        write_audit(
            db, user=manager, action="dataset_activate", target=name, request=request
        )
        db.commit()
        db.refresh(dataset)

    return _to_out(dataset)


@router.post(
    "/{dataset_id}/activate",
    response_model=DatasetOut,
    summary="Сделать датасет основным (перезагрузка детектора)",
)
def activate_dataset(
    dataset_id: int,
    request: Request,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> DatasetOut:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Датасет не найден")

    path = dataset_manager.absolute_path(dataset)
    if not path.exists():
        raise HTTPException(
            status_code=410,
            detail="Файл весов отсутствует на диске. Перезагрузите датасет.",
        )

    backend = dataset_manager.activate(db, dataset)
    write_audit(
        db,
        user=manager,
        action="dataset_activate",
        target=dataset.name,
        details=f"backend={backend}",
        request=request,
    )
    db.commit()
    db.refresh(dataset)
    return _to_out(dataset)


@router.post(
    "/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Сбросить активный датасет (вернуться к весам из конфига)",
)
def deactivate_active(
    request: Request,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    dataset_manager.deactivate_all(db)
    write_audit(db, user=manager, action="dataset_deactivate_all", request=request)
    db.commit()


@router.delete(
    "/{dataset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удаление датасета (руководитель)",
)
def delete_dataset(
    dataset_id: int,
    request: Request,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
):
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Датасет не найден")
    if dataset.is_active:
        dataset.is_active = False
        db.flush()
        dataset_manager.reload_detector_to_preferred_weights(db)
    name = dataset.name
    dataset_manager.remove_files(dataset)
    db.delete(dataset)
    write_audit(db, user=manager, action="dataset_delete", target=name, request=request)
    db.commit()
