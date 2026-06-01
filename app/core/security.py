"""Подсистема безопасности: хеширование паролей и работа с JWT.

Реализует требования ТЗ п. 4.8.1:

* хранение паролей в виде bcrypt-хешей с фактором сложности не ниже 12;
* JWT-аутентификация с передачей токена в заголовке ``Authorization``;
* срок действия токена — не более 8 часов.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from ..config import settings
from ..models.user import UserRole


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.bcrypt_rounds,
)


def hash_password(password: str) -> str:
    """Хеширует пароль bcrypt-ом с параметром сложности из настроек."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверяет открытый пароль по сохранённому хешу."""
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        # Невалидный формат хеша — считаем проверку неуспешной.
        return False


def create_access_token(*, user_id: int, username: str, role: UserRole) -> tuple[str, int]:
    """Формирует JWT-токен.

    Возвращает кортеж ``(token, expires_in_seconds)``.
    """
    expires_delta = timedelta(minutes=settings.access_token_expire_minutes)
    expire_at = datetime.now(timezone.utc) + expires_delta
    payload: dict[str, Any] = {
        "sub": username,
        "uid": user_id,
        "role": role.value,
        "exp": int(expire_at.timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, int(expires_delta.total_seconds())


def decode_token(token: str) -> dict[str, Any]:
    """Декодирует JWT-токен.

    :raises JWTError: если токен невалиден или просрочен.
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


__all__ = [
    "JWTError",
    "create_access_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
