"""Модели пользователя и попыток входа.

Реализует ролевую модель ТЗ п. 4.8.3 (оператор, руководитель) и роль
«администратор» для настроек платформы, учётных записей и эталонов.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class UserRole(str, enum.Enum):
    """Роли пользователей (ТЗ п. 4.8.3 + отдельный администратор платформы)."""

    OPERATOR = "operator"        # Сотрудник (оператор)
    MANAGER = "manager"        # Руководитель (производство, журнал, датасеты)
    ADMIN = "admin"            # Администратор (пользователи, настройки, эталоны, опасные операции)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=16),
        nullable=False,
        default=UserRole.OPERATOR,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    inspections: Mapped[list["Inspection"]] = relationship(  # noqa: F821
        back_populates="operator",
        cascade="all,delete-orphan",
        foreign_keys="Inspection.operator_id",
    )

    def __repr__(self) -> str:  # pragma: no cover - служебное
        return f"<User {self.username} ({self.role.value})>"


class LoginAttempt(Base):
    """Журнал попыток входа — используется для блокировки (ТЗ 4.8.1)."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )
