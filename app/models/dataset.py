"""Модель датасета (весов модели детекции).

Руководитель загружает файл весов (``*.pt``) и регистрирует его в системе.
В любой момент времени может быть активен только один датасет — его
использует детектор для анализа плат. Переключение активного датасета
вызывает «горячую» перезагрузку весов в синглтоне ``Detector``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Относительно BASE_DIR, например: "models/datasets/3/weights.pt".
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    original_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    uploaded_by: Mapped["User | None"] = relationship(  # noqa: F821
        foreign_keys=[uploaded_by_id]
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Dataset {self.name!r} active={self.is_active}>"
