"""Маршруты управления устройствами и трансляцией видеопотока.

Сценарии:

1. *Эксклюзивное закрепление*. Оператор «берёт в работу» устройство
   (/take). Пока занято — другие операторы взять не могут.

2. *Удалённая камера с телефона*. Руководитель из админки создаёт запись
   устройства — система автоматически генерирует ``upload_token`` и формирует
   ссылку ``/phone?device=<id>&token=<upload_token>``. Эту ссылку (или QR)
   администратор передаёт сотруднику с телефоном; телефон открывает её,
   разрешает камеру и начинает публиковать кадры через
   ``POST /api/devices/{id}/frame`` с заголовком ``X-Device-Token``.

3. *Просмотр потока*. Любой авторизованный пользователь получает живое
   видео с устройства как ``multipart/x-mixed-replace`` через
   ``GET /api/devices/{id}/stream?token=<JWT>`` или одиночные кадры через
   ``GET /api/devices/{id}/frame.jpg``. Для ``<img src=…>`` в HTML удобно
   использовать MJPEG с токеном в query-параметре.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import effective_public_base_url, settings
from ..core.long_lived_stream import LongLivedStreamingResponse
from ..core.security import decode_token
from ..database import get_db
from ..models import Device, User, UserRole
from ..schemas import (
    DeviceCommand,
    DeviceCreate,
    DeviceOut,
    DeviceStatusIn,
    DeviceStatusOut,
    DeviceUpdate,
)
from ..services.command_queue import ALLOWED_COMMANDS, command_queue
from ..database import SessionLocal
from ..services.device_registry_events import (
    notify_device_registry_changed,
    subscribe_registry,
    unsubscribe_registry,
)
from ..services.frame_events import (
    publish_device_status,
    publish_frame_ready,
    subscribe,
    unsubscribe,
)
from ..services.stream_store import stream_store
from .deps import has_backoffice_role, require_admin, require_any, require_manager, write_audit


router = APIRouter(prefix="/api/devices", tags=["Устройства"])


def _build_device_status_out(device_id: int) -> DeviceStatusOut:
    """Снимок для GET /status и для SSE без повторного кода."""
    st = command_queue.get_status(device_id)
    frame = stream_store.get(device_id)
    return DeviceStatusOut(
        is_streaming=bool(st and st.is_streaming),
        preset=st.preset if st else None,
        torch_on=bool(st and st.torch_on),
        facing=st.facing if st else None,
        updated_at=st.updated_at if st else None,
        frame_received_at=frame.received_at if frame else None,
        frame_available=frame is not None,
    )


# Окно, в течение которого устройство считается «в эфире» от последнего кадра.
STREAMING_ACTIVE_SECONDS = 10
# Максимальный размер одного принимаемого кадра.
MAX_FRAME_BYTES = 12 * 1024 * 1024


def _device_query_options():
    return (
        selectinload(Device.assigned_operator),
        selectinload(Device.designated_operator),
        selectinload(Device.registered_by),
    )


def _devices_stmt_for_user(user: User):
    stmt = select(Device).options(*_device_query_options()).order_by(Device.name)
    if user.role == UserRole.OPERATOR:
        stmt = stmt.where(Device.designated_operator_id == user.id)
    return stmt


def _operator_can_use_device(device: Device, user: User) -> bool:
    if user.role != UserRole.OPERATOR:
        return True
    return device.designated_operator_id == user.id


def _validate_designated_operator(db: Session, operator_id: int | None) -> None:
    if operator_id is None:
        return
    op = db.get(User, operator_id)
    if op is None or op.role != UserRole.OPERATOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Закрепить камеру можно только за сотрудником (оператором)",
        )


def _is_streaming(device: Device) -> bool:
    if device.last_seen_at is None:
        return False
    return (datetime.utcnow() - device.last_seen_at) < timedelta(
        seconds=STREAMING_ACTIVE_SECONDS
    )


def _to_out(
    device: Device,
    *,
    include_token: bool = False,
    base_url: str | None = None,
) -> DeviceOut:
    token = device.upload_token if include_token else None
    link = None
    if include_token and base_url and device.upload_token:
        link = f"{base_url.rstrip('/')}/phone?device={device.id}&token={device.upload_token}"
    return DeviceOut(
        id=device.id,
        name=device.name,
        identifier=device.identifier,
        description=device.description,
        is_active=device.is_active,
        assigned_operator_id=device.assigned_operator_id,
        assigned_operator_username=(
            device.assigned_operator.username if device.assigned_operator else None
        ),
        assigned_at=device.assigned_at,
        designated_operator_id=device.designated_operator_id,
        designated_operator_username=(
            device.designated_operator.username if device.designated_operator else None
        ),
        registered_by_id=device.registered_by_id,
        registered_by_username=(
            device.registered_by.username if device.registered_by else None
        ),
        last_seen_at=device.last_seen_at,
        is_streaming=_is_streaming(device),
        created_at=device.created_at,
        upload_token=token,
        streaming_link=link,
        has_upload_token=bool(device.upload_token),
    )


def _load(db: Session, device_id: int) -> Device:
    device = db.execute(
        select(Device)
        .options(*_device_query_options())
        .execution_options(populate_existing=True)
        .where(Device.id == device_id)
    ).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено")
    return device


def _gen_token() -> str:
    return secrets.token_urlsafe(32)


def _base_url(request: Request) -> str:
    """Базовый URL для QR/ссылки на телефон (HTTPS + хост, доступный из Wi‑Fi)."""
    pub = effective_public_base_url()
    if pub:
        return pub
    origin = request.headers.get("origin") or ""
    if origin.startswith("http"):
        return origin.rstrip("/")
    ref = request.headers.get("referer") or ""
    if ref.startswith("http"):
        p = urlparse(ref)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}".rstrip("/")
    xf_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    xf_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if xf_host and xf_proto:
        return f"{xf_proto}://{xf_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("", response_model=list[DeviceOut], summary="Список устройств")
def list_devices(
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> list[DeviceOut]:
    rows = db.execute(_devices_stmt_for_user(user)).scalars().all()
    return [_to_out(d) for d in rows]


@router.post(
    "",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Добавление устройства (руководитель)",
)
def create_device(
    payload: DeviceCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> DeviceOut:
    """Создаёт устройство и сразу выдаёт upload_token + ссылку для телефона.

    В ответе один раз показываются секреты — сохраните ссылку/QR, чтобы
    передать их сотруднику с телефоном. Потом токен можно увидеть только
    через регенерацию ``POST /{id}/regenerate-token``.
    """
    existing = db.execute(
        select(Device).where(Device.name == payload.name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Устройство с таким именем уже существует")

    device = Device(
        name=payload.name,
        identifier=payload.identifier,
        description=payload.description,
        upload_token=_gen_token(),
        registered_by_id=manager.id,
    )
    db.add(device)
    write_audit(
        db,
        user=manager,
        action="device_create",
        target=payload.name,
        details=payload.identifier,
        request=request,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Конфликт уникальности (имя/идентификатор)")
    background_tasks.add_task(notify_device_registry_changed)
    device = _load(db, device.id)
    return _to_out(device, include_token=True, base_url=_base_url(request))


@router.patch(
    "/{device_id}",
    response_model=DeviceOut,
    summary="Изменение устройства (руководитель)",
)
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = _load(db, device_id)

    changes: list[str] = []
    for field in ("name", "identifier", "description", "is_active"):
        value = getattr(payload, field)
        if value is not None and getattr(device, field) != value:
            setattr(device, field, value)
            changes.append(f"{field}={value}")

    if "designated_operator_id" in payload.model_fields_set:
        new_op = payload.designated_operator_id
        _validate_designated_operator(db, new_op)
        if device.designated_operator_id != new_op:
            if (
                device.assigned_operator_id is not None
                and device.assigned_operator_id != new_op
            ):
                device.assigned_operator_id = None
                device.assigned_at = None
                changes.append("assigned_released_on_redesignate")
            device.designated_operator_id = new_op
            changes.append(f"designated_operator_id={new_op}")

    if changes:
        write_audit(
            db,
            user=manager,
            action="device_update",
            target=device.name,
            details=", ".join(changes),
            request=request,
        )
    db.commit()
    background_tasks.add_task(notify_device_registry_changed)
    device = _load(db, device.id)
    return _to_out(device)


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удаление устройства (администратор)",
)
def delete_device(
    device_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    device = _load(db, device_id)
    name = device.name
    stream_store.drop(device.id)
    command_queue.clear(device.id)
    db.delete(device)
    write_audit(db, user=admin, action="device_delete", target=name, request=request)
    db.commit()
    background_tasks.add_task(notify_device_registry_changed)


@router.post(
    "/{device_id}/regenerate-token",
    response_model=DeviceOut,
    summary="Сгенерировать новый upload-токен и ссылку для телефона (руководитель)",
)
def regenerate_token(
    device_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = _load(db, device_id)
    device.upload_token = _gen_token()
    device.last_seen_at = None
    stream_store.drop(device.id)
    command_queue.clear(device.id)
    write_audit(
        db,
        user=manager,
        action="device_regenerate_token",
        target=device.name,
        request=request,
    )
    db.commit()
    background_tasks.add_task(notify_device_registry_changed)
    device = _load(db, device.id)
    return _to_out(device, include_token=True, base_url=_base_url(request))


@router.get(
    "/{device_id}/link",
    response_model=DeviceOut,
    summary="Получить актуальную ссылку трансляции (руководитель)",
)
def get_link(
    device_id: int,
    request: Request,
    manager: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = _load(db, device_id)
    return _to_out(device, include_token=True, base_url=_base_url(request))


# ---------------------------------------------------------------------------
# «Моё устройство»
# ---------------------------------------------------------------------------
@router.get(
    "/mine",
    response_model=DeviceOut | None,
    summary="Занятое мной устройство (если есть)",
)
def my_device(
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> DeviceOut | None:
    device = db.execute(
        select(Device)
        .options(*_device_query_options())
        .where(Device.assigned_operator_id == user.id)
    ).scalar_one_or_none()
    if device and not _operator_can_use_device(device, user):
        return None
    return _to_out(device) if device else None


def _registry_sse_payload(user_id: int) -> dict:
    """Полный список устройств + «моё» для SSE (отдельная сессия БД на каждый снимок)."""
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if u is None or not u.is_active:
            return {"type": "registry", "devices": [], "mine": None}
        rows = db.execute(_devices_stmt_for_user(u)).scalars().all()
        devices = [_to_out(d).model_dump(mode="json") for d in rows]
        mine_dev = db.execute(
            select(Device)
            .options(*_device_query_options())
            .where(Device.assigned_operator_id == user_id)
        ).scalar_one_or_none()
        if mine_dev and not _operator_can_use_device(mine_dev, u):
            mine_dev = None
        mine = _to_out(mine_dev).model_dump(mode="json") if mine_dev else None
        return {"type": "registry", "devices": devices, "mine": mine}


@router.get(
    "/registry-events",
    summary="SSE: снимок списка устройств и закрепления (без опроса GET)",
)
async def device_registry_events_stream(
    token: str = Query(..., description="JWT access_token (как у frame-events)"),
):
    """События при take/release/CRUD; первый кадр — текущее состояние."""

    with SessionLocal() as db:
        user = _verify_jwt_from_query(token, db)
        uid = int(user.id)

    async def event_generator():
        q = await subscribe_registry()
        try:
            yield (
                "data: "
                + json.dumps(_registry_sse_payload(uid), default=str)
                + "\n\n"
            )
            while True:
                try:
                    await asyncio.wait_for(q.get(), timeout=55.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield (
                    "data: "
                    + json.dumps(_registry_sse_payload(uid), default=str)
                    + "\n\n"
                )
        finally:
            await unsubscribe_registry(q)

    return LongLivedStreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Захват/освобождение
# ---------------------------------------------------------------------------
@router.post(
    "/{device_id}/take",
    response_model=DeviceOut,
    summary="Взять устройство в работу (эксклюзивный захват)",
)
def take_device(
    device_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = _load(db, device_id)
    if not device.is_active:
        raise HTTPException(status_code=400, detail="Устройство выведено из эксплуатации")
    if not _operator_can_use_device(device, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Эта камера не закреплена за вами. Обратитесь к руководителю.",
        )

    current = db.execute(
        select(Device).where(Device.assigned_operator_id == user.id)
    ).scalar_one_or_none()
    if current is not None and current.id != device.id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"У вас уже занято устройство «{current.name}». "
                "Сначала освободите его."
            ),
        )

    if device.assigned_operator_id is not None and device.assigned_operator_id != user.id:
        raise HTTPException(
            status_code=409,
            detail="Устройство занято другим оператором",
        )

    device.assigned_operator_id = user.id
    device.assigned_at = datetime.utcnow()
    write_audit(db, user=user, action="device_take", target=device.name, request=request)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Устройство только что занято другим оператором")
    background_tasks.add_task(notify_device_registry_changed)
    device = _load(db, device.id)
    return _to_out(device)


@router.post(
    "/{device_id}/release",
    response_model=DeviceOut,
    summary="Освободить устройство",
)
def release_device(
    device_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = _load(db, device_id)
    if device.assigned_operator_id is None:
        return _to_out(device)
    if not has_backoffice_role(user) and device.assigned_operator_id != user.id:
        raise HTTPException(status_code=403, detail="Можно освобождать только собственное устройство")

    device.assigned_operator_id = None
    device.assigned_at = None
    write_audit(db, user=user, action="device_release", target=device.name, request=request)
    db.commit()
    background_tasks.add_task(notify_device_registry_changed)
    device = _load(db, device.id)
    return _to_out(device)


# ---------------------------------------------------------------------------
# Публичное API для телефона (без JWT)
# ---------------------------------------------------------------------------
@router.get(
    "/public/{device_id}",
    response_model=DeviceOut,
    summary="Инфо об устройстве для страницы телефона (требуется upload-токен)",
)
def public_info(
    device_id: int,
    token: str = Query(..., description="upload_token"),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = db.get(Device, device_id)
    if device is None or device.upload_token != token:
        raise HTTPException(status_code=401, detail="Неверный токен устройства")
    device = _load(db, device_id)
    return _to_out(device)


@router.post(
    "/{device_id}/frame",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Публикация кадра устройством (X-Device-Token)",
    include_in_schema=True,
)
async def publish_frame(
    device_id: int,
    image: UploadFile = File(...),
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
) -> Response:
    """Принимает JPEG-кадр от устройства. Авторизация — по upload_token.

    Эндпоинт НАМЕРЕННО не требует JWT — телефон может публиковать часами,
    пока валиден upload_token. Токен можно отозвать через
    ``/regenerate-token`` или удалив устройство.
    """
    if not x_device_token:
        raise HTTPException(status_code=401, detail="Требуется X-Device-Token")

    device = db.get(Device, device_id)
    if device is None or device.upload_token != x_device_token:
        raise HTTPException(status_code=401, detail="Неверный токен устройства")
    if not device.is_active:
        raise HTTPException(status_code=400, detail="Устройство выведено из эксплуатации")

    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой кадр")
    if len(data) > MAX_FRAME_BYTES:
        raise HTTPException(status_code=413, detail="Кадр превышает лимит размера")

    content_type = image.content_type or "image/jpeg"
    stream_store.put(device_id, data, content_type=content_type)
    device.last_seen_at = datetime.utcnow()
    db.commit()
    await publish_frame_ready(device_id)
    status_out = _build_device_status_out(device_id)
    await publish_device_status(device_id, status_out.model_dump(mode="json"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Просмотр кадров (оператор ПК): единичный кадр и MJPEG-стрим
# ---------------------------------------------------------------------------
@router.get(
    "/{device_id}/frame.jpg",
    summary="Получение последнего кадра устройства",
)
def get_frame(
    device_id: int,
    _: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено")

    frame = stream_store.get(device_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Кадр ещё не получен")

    return Response(
        content=frame.data,
        media_type=frame.content_type,
        headers={
            "Cache-Control": "no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Frame-Received-At": frame.received_at.isoformat(),
        },
    )


@router.get(
    "/{device_id}/frame-events",
    summary="SSE: кадры и статус устройства (без опроса GET /status)",
)
async def frame_events_stream(
    device_id: int,
    token: str = Query(..., description="JWT access_token (как у /stream)"),
    db: Session = Depends(get_db),
):
    """Server-Sent Events: push при новом кадре и при смене статуса с телефона.

    Сообщения ``type: status`` — полный объект как у ``GET /api/devices/{id}/status``.
    Авторизация через ``token`` в query (EventSource не шлёт Authorization).
    """
    _verify_jwt_from_query(token, db)
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено")

    async def event_generator():
        q = await subscribe(device_id)
        try:
            snap = _build_device_status_out(device_id)
            yield (
                "data: "
                + json.dumps(
                    {"type": "status", "status": snap.model_dump(mode="json")},
                )
                + "\n\n"
            )
            if stream_store.get(device_id) is not None:
                yield f"data: {json.dumps({'type': 'frame', 'available': True})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25.0)
                    kind = msg.get("kind", "frame")
                    if kind == "status":
                        data = msg["data"]
                        yield (
                            "data: "
                            + json.dumps({"type": "status", "status": data})
                            + "\n\n"
                        )
                    else:
                        yield f"data: {json.dumps({'type': 'frame', 'available': True})}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await unsubscribe(device_id, q)

    return LongLivedStreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Удалённое управление телефоном: PC → очередь команд → телефон
# ---------------------------------------------------------------------------
def _can_control_device(device: Device, user: User) -> bool:
    if user.role in (UserRole.MANAGER, UserRole.ADMIN):
        return True
    return device.assigned_operator_id == user.id


@router.post(
    "/{device_id}/control",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Отправить команду телефону (владелец устройства или руководитель)",
)
def send_command(
    device_id: int,
    payload: DeviceCommand,
    request: Request,
    user: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> dict:
    device = _load(db, device_id)
    if not _can_control_device(device, user):
        raise HTTPException(
            status_code=403,
            detail="Управлять устройством может только оператор, взявший его в работу, или руководитель.",
        )
    try:
        cmd = command_queue.enqueue(device_id, payload.command, payload.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_audit(
        db,
        user=user,
        action="device_command",
        target=device.name,
        details=f"{payload.command}{'=' + payload.value if payload.value else ''}",
        request=request,
    )
    db.commit()
    return {"queued": True, "command": cmd.to_dict()}


@router.get(
    "/{device_id}/status",
    response_model=DeviceStatusOut,
    summary="Статус телефона (стрим, подсветка, качество) для PC-UI",
)
def device_status(
    device_id: int,
    _: User = Depends(require_any),
    db: Session = Depends(get_db),
) -> DeviceStatusOut:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено")
    return _build_device_status_out(device_id)


@router.get(
    "/{device_id}/commands",
    summary="Опрос команд телефоном (X-Device-Token). Возвращает и очищает очередь.",
)
def fetch_commands(
    device_id: int,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not x_device_token:
        raise HTTPException(status_code=401, detail="Требуется X-Device-Token")
    device = db.get(Device, device_id)
    if device is None or device.upload_token != x_device_token:
        raise HTTPException(status_code=401, detail="Неверный токен устройства")
    commands = command_queue.drain(device_id)
    return {
        "commands": [c.to_dict() for c in commands],
        "allowed": {name: sorted(v) if v else None for name, v in ALLOWED_COMMANDS.items()},
    }


@router.post(
    "/{device_id}/status",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Обновление статуса телефона (X-Device-Token)",
)
async def report_status(
    device_id: int,
    payload: DeviceStatusIn,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    db: Session = Depends(get_db),
) -> Response:
    if not x_device_token:
        raise HTTPException(status_code=401, detail="Требуется X-Device-Token")
    device = db.get(Device, device_id)
    if device is None or device.upload_token != x_device_token:
        raise HTTPException(status_code=401, detail="Неверный токен устройства")
    command_queue.set_status(
        device_id,
        is_streaming=payload.is_streaming,
        preset=payload.preset,
        torch_on=payload.torch_on,
        facing=payload.facing,
    )
    status_out = _build_device_status_out(device_id)
    await publish_device_status(device_id, status_out.model_dump(mode="json"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _verify_jwt_from_query(token: str, db: Session) -> User:
    try:
        payload = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Невалидный токен")
    uid = payload.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Невалидный токен")
    user = db.get(User, int(uid))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Сеанс недействителен")
    return user


@router.get(
    "/{device_id}/stream",
    summary="MJPEG-трансляция устройства (multipart/x-mixed-replace)",
    response_class=LongLivedStreamingResponse,
)
async def stream_device(
    device_id: int,
    token: str = Query(..., description="JWT access_token оператора"),
    db: Session = Depends(get_db),
):
    """Отдаёт живое видео в формате ``multipart/x-mixed-replace``.

    Такой ответ можно напрямую вставить в ``<img src=…>``: браузер будет
    плавно обновлять картинку по мере поступления новых кадров. Частота
    полностью определяется темпом публикации с устройства (телефон).

    Авторизация — через JWT в параметре ``token`` (необходимо, т.к.
    ``<img>`` не поддерживает HTTP-заголовки).
    """
    _verify_jwt_from_query(token, db)
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Устройство не найдено")

    boundary = b"aoiframe"

    async def generator():
        last_ts = None
        # Отдаём первый кадр, как только появится (чтобы не висеть пустой img).
        first_sent = False
        try:
            while True:
                frame = stream_store.get(device_id)
                if frame is not None and frame.received_at != last_ts:
                    last_ts = frame.received_at
                    header = (
                        b"--" + boundary + b"\r\n"
                        b"Content-Type: " + frame.content_type.encode() + b"\r\n"
                        b"Content-Length: " + str(len(frame.data)).encode() + b"\r\n"
                        b"X-Frame-Received-At: " + frame.received_at.isoformat().encode() + b"\r\n\r\n"
                    )
                    yield header + frame.data + b"\r\n"
                    first_sent = True
                # Мгновенный отклик на новые кадры + защита от busy-loop.
                await asyncio.sleep(0.04 if first_sent else 0.15)
        except asyncio.CancelledError:  # клиент отключился
            raise

    return LongLivedStreamingResponse(
        generator(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary.decode()}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",  # подсказка nginx не буферизовать
            "Connection": "close",
        },
    )
