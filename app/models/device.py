"""Модель устройства (смартфон/планшет/камера).

Поддерживает эксклюзивную занятость: если устройство «взято в работу» оператором,
другие операторы не могут его использовать, пока оно не освобождено (или пока
руководитель не снимет закрепление).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    # Идентификатор устройства — IMEI/серийный номер/MAC и т.п. Может отсутствовать.
    identifier: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Если занято — здесь ид. оператора. UNIQUE на уровне БД гарантирует,
    # что в один момент времени устройство привязано не более чем к одному
    # оператору (NULL разрешён многократно для свободных устройств).
    assigned_operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Постоянное закрепление камеры за сотрудником (назначает руководитель).
    # Оператор видит и может взять в работу только устройства с этим полем = его id.
    designated_operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Токен для публикации кадров
    # X-Device-Token при вызове /frame). Знает только хозяин устройства.
    upload_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    # Кто зарегистрировал устройство из своего браузера.
    registered_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Момент приёма последнего кадра. Актуальность трансляции определяется
    # на стороне сервера (см. STREAMING_ACTIVE_SECONDS в API).
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    assigned_operator: Mapped["User | None"] = relationship(  # noqa: F821
        foreign_keys=[assigned_operator_id]
    )
    designated_operator: Mapped["User | None"] = relationship(  # noqa: F821
        foreign_keys=[designated_operator_id]
    )
    registered_by: Mapped["User | None"] = relationship(  # noqa: F821
        foreign_keys=[registered_by_id]
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Device {self.name!r} assigned_to={self.assigned_operator_id}>"
