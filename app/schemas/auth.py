"""Pydantic-схемы для аутентификации."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models.user import UserRole


class LoginRequest(BaseModel):
    """Тело запроса на вход."""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """Ответ на успешный вход: JWT и информация о пользователе."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # секунды
    role: UserRole
    username: str
    full_name: str


class TokenPayload(BaseModel):
    """Декодированное содержимое JWT."""

    sub: str           # username
    uid: int           # user id
    role: UserRole
    exp: int
