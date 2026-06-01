"""Администрирование пользователей (ТЗ Ф11).

Просмотр списка — руководитель или администратор.
Создание, смена роли и удаление — только администратор.
Блокировка/разблокировка (``is_active``, снятие ``locked_until``) — руководитель или
администратор; руководитель **не может** блокировать учётную запись администратора.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.security import hash_password, verify_password
from ..database import get_db
from ..models import User, UserRole
from ..schemas import UserCreate, UserOut, UserPasswordChange, UserPasswordSet, UserUpdate
from .deps import get_current_user, require_admin, require_manager, write_audit

_MANAGER_BLOCK_FORBIDDEN = "Руководитель не может блокировать администратора"


router = APIRouter(prefix="/api/users", tags=["Пользователи"])


def _manager_cannot_block_admin(actor: User, target: User) -> None:
    if actor.role == UserRole.MANAGER and target.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_MANAGER_BLOCK_FORBIDDEN,
        )


def _count_admins(db: Session, *, exclude_user_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return int(db.scalar(stmt) or 0)


@router.get("", response_model=list[UserOut], summary="Список пользователей")
def list_users(
    _: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> list[User]:
    return list(db.execute(select(User).order_by(User.username)).scalars().all())


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создание пользователя (администратор)",
)
def create_user(
    payload: UserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    existing = db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким логином уже существует",
        )
    user = User(
        username=payload.username,
        full_name=payload.full_name,
        role=payload.role,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    write_audit(
        db,
        user=admin,
        action="user_create",
        target=payload.username,
        details=f"role={payload.role.value}",
        request=request,
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    summary="Обновление пользователя (администратор или блокировка руководителем)",
)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    actor: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if actor.role == UserRole.MANAGER:
        if payload.full_name is not None or payload.role is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Руководитель может только блокировать или разблокировать пользователей",
            )
        if payload.is_active is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите is_active для блокировки или разблокировки",
            )
        _manager_cannot_block_admin(actor, user)
        if payload.is_active is False and user.id == actor.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя заблокировать собственную учётную запись",
            )

    if payload.role is not None and user.role == UserRole.ADMIN and payload.role != UserRole.ADMIN:
        if _count_admins(db, exclude_user_id=user.id) < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя снять роль администратора с последней учётной записи admin",
            )

    changes: list[str] = []
    if payload.full_name is not None and payload.full_name != user.full_name:
        user.full_name = payload.full_name
        changes.append("full_name")
    if payload.role is not None and payload.role != user.role:
        user.role = payload.role
        changes.append(f"role={payload.role.value}")
    if payload.is_active is not None and payload.is_active != user.is_active:
        user.is_active = payload.is_active
        changes.append(f"is_active={payload.is_active}")
        if payload.is_active:
            user.locked_until = None

    if changes:
        write_audit(
            db,
            user=actor,
            action="user_update",
            target=user.username,
            details=", ".join(changes),
            request=request,
        )
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/{user_id}/unlock",
    response_model=UserOut,
    summary="Снятие блокировки учётной записи (руководитель или администратор)",
)
def unlock_user(
    user_id: int,
    request: Request,
    actor: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    _manager_cannot_block_admin(actor, user)
    user.locked_until = None
    user.is_active = True
    write_audit(db, user=actor, action="user_unlock", target=user.username, request=request)
    db.commit()
    db.refresh(user)
    return user


@router.delete(
    "/{user_id}",
    summary="Удаление пользователя (администратор)",
)
def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить собственную учётную запись",
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if user.role == UserRole.ADMIN and _count_admins(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить последнего администратора системы",
        )

    uname = user.username
    db.delete(user)
    write_audit(
        db,
        user=admin,
        action="user_delete",
        target=uname,
        request=request,
    )
    db.commit()
    return {"detail": "Пользователь удалён"}


@router.post(
    "/{user_id}/password",
    summary="Назначение нового пароля пользователю (администратор)",
)
def set_user_password(
    user_id: int,
    payload: UserPasswordSet,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.hashed_password = hash_password(payload.new_password)
    write_audit(
        db,
        user=admin,
        action="user_password_set",
        target=user.username,
        request=request,
    )
    db.commit()
    return {"detail": "Пароль пользователя обновлён"}


@router.post(
    "/me/password",
    summary="Смена собственного пароля",
)
def change_password(
    payload: UserPasswordChange,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if not verify_password(payload.old_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Старый пароль указан неверно",
        )
    user.hashed_password = hash_password(payload.new_password)
    write_audit(db, user=user, action="password_change", request=request)
    db.commit()
    return {"detail": "Пароль успешно изменён"}
