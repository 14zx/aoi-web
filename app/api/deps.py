"""FastAPI-зависимости: аутентификация, проверка ролей, аудит (ТЗ п. 4.8.3)."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from ..core.security import JWTError, decode_token
from ..database import get_db
from ..models import AuditLog, User, UserRole


BACKOFFICE_ROLES: frozenset[UserRole] = frozenset({UserRole.MANAGER, UserRole.ADMIN})


def has_backoffice_role(user: User) -> bool:
    """Руководитель или администратор (доступ к журналу всех инспекций и т.п.)."""
    return user.role in BACKOFFICE_ROLES


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Возвращает текущего пользователя по JWT-токену."""
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется авторизация",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exc
    try:
        payload = decode_token(token)
    except JWTError:
        raise credentials_exc from None

    user_id = payload.get("uid")
    if not isinstance(user_id, int):
        raise credentials_exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Учётная запись заблокирована или удалена",
        )
    return user


def require_role(*roles: UserRole) -> Callable[..., User]:
    """Фабрика зависимости для проверки роли (ТЗ 4.8.3)."""
    allowed: Iterable[UserRole] = roles

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для выполнения операции",
            )
        return user

    return _dep


require_manager = require_role(UserRole.MANAGER, UserRole.ADMIN)
require_admin = require_role(UserRole.ADMIN)
require_any = require_role(UserRole.OPERATOR, UserRole.MANAGER, UserRole.ADMIN)


def write_audit(
    db: Session,
    *,
    user: User | None,
    action: str,
    target: str | None = None,
    details: str | None = None,
    request: Request | None = None,
) -> None:
    """Добавляет запись в журнал аудита (ТЗ 4.8.1) без коммита."""
    ip = None
    if request is not None and request.client is not None:
        ip = request.client.host
    record = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else None,
        action=action,
        target=target,
        details=details,
        ip_address=ip,
    )
    db.add(record)
