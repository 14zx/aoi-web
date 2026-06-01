"""Маршруты аутентификации (функция Ф1 ТЗ).

Реализует:
* вход по логину/паролю (ТЗ 4.1.1);
* учёт и блокировку учётной записи после 5 неудачных попыток на 15 минут
  (ТЗ 4.8.1);
* выпуск JWT-токена (ТЗ 4.5.4);
* запись событий в журнал аудита.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.security import create_access_token, verify_password
from ..database import get_db
from ..models import LoginAttempt, User
from ..schemas import TokenResponse, UserOut
from .deps import get_current_user, write_audit


router = APIRouter(prefix="/api/auth", tags=["Аутентификация"])


def _record_attempt(
    db: Session, *, user: User | None, username: str, success: bool, request: Request
) -> None:
    attempt = LoginAttempt(
        user_id=user.id if user else None,
        username=username,
        success=success,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:255] or None,
    )
    db.add(attempt)


def _apply_lockout(db: Session, user: User) -> None:
    """Блокирует учётку, если недавно было ≥ N неуспешных попыток подряд.

    Работаем в «наивном» UTC (без tzinfo), так как SQLite/SQLAlchemy возвращают
    ``created_at`` без таймзоны — это позволяет корректно сравнивать значения.
    """
    # Убеждаемся, что последняя попытка уже зафиксирована в сессии.
    db.flush()
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=settings.login_lockout_minutes)
    recent = (
        db.execute(
            select(LoginAttempt)
            .where(
                LoginAttempt.username == user.username,
                LoginAttempt.created_at >= window_start,
            )
            .order_by(LoginAttempt.created_at.desc())
            .limit(settings.max_login_attempts)
        )
        .scalars()
        .all()
    )
    if (
        len(recent) >= settings.max_login_attempts
        and all(not a.success for a in recent)
    ):
        user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Вход в систему (Ф1)",
)
def login(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Вход по логину/паролю. Форма ``x-www-form-urlencoded``: ``username``, ``password``."""
    user = db.execute(
        select(User).where(User.username == form.username)
    ).scalar_one_or_none()

    # Проверка блокировки.
    if user and user.locked_until and user.locked_until > datetime.utcnow():
        _record_attempt(db, user=user, username=form.username, success=False, request=request)
        write_audit(
            db,
            user=user,
            action="login_denied_locked",
            target=user.username,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                "Учётная запись заблокирована до "
                f"{user.locked_until.isoformat(timespec='seconds')}"
            ),
        )

    if not user or not user.is_active or not verify_password(form.password, user.hashed_password):
        _record_attempt(db, user=user, username=form.username, success=False, request=request)
        if user:
            _apply_lockout(db, user)
        write_audit(
            db,
            user=user,
            action="login_failed",
            target=form.username,
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    # Успешный вход — сбрасываем блокировку.
    user.locked_until = None
    _record_attempt(db, user=user, username=form.username, success=True, request=request)
    write_audit(db, user=user, action="login_success", request=request)
    db.commit()

    token, expires_in = create_access_token(
        user_id=user.id, username=user.username, role=user.role
    )
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        role=user.role,
        username=user.username,
        full_name=user.full_name,
    )


@router.get(
    "/me",
    response_model=UserOut,
    summary="Информация о текущем пользователе",
)
def read_me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout", summary="Выход (ротация токена на клиенте)")
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Фиксирует выход в журнале аудита.

    JWT является stateless-токеном, фактический logout выполняется клиентом
    путём удаления токена из локального хранилища.
    """
    write_audit(db, user=user, action="logout", request=request)
    db.commit()
    return {"detail": "ok"}
