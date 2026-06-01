"""Эталонные профили плат (Golden Board Manager, ТЗ п. 5)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class GoldenBoardProfile(Base):
    """Сохранённый эталон: JSON с рамками/классами и метаданные."""

    __tablename__ = "golden_board_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    board_model: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)

    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    designated_operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )

    author: Mapped["User | None"] = relationship(  # noqa: F821
        foreign_keys=[author_id],
    )
    designated_operator: Mapped["User | None"] = relationship(  # noqa: F821
        foreign_keys=[designated_operator_id],
    )
