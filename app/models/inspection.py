"""Модели инспекций и обнаруженных дефектов."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class InspectionStatus(str, enum.Enum):
    """Статус прохождения инспекции."""

    SUCCESS = "success"   # Детекция выполнена успешно
    FAILED = "failed"     # Ошибка в ходе инференса/предобработки
    PENDING = "pending"   # Создана, но ещё не обработана


class Inspection(Base):
    """Протокол инспекции одного изображения."""

    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Устройство, с которого выполнена инспекция (опц., ТЗ Ф2).
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Относительные пути внутри каталога storage/.
    original_path: Mapped[str] = mapped_column(String(255), nullable=False)
    result_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[InspectionStatus] = mapped_column(
        Enum(InspectionStatus, native_enum=False, length=16),
        nullable=False,
        default=InspectionStatus.PENDING,
    )
    defects_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Общая достоверность (среднее по дефектам, 0..1) — информативный показатель.
    avg_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Время инференса, мс — контроль требования ТЗ 4.1.3.
    inference_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Порог достоверности, применявшийся при детекции (фиксируется в протоколе).
    conf_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Необязательная метка модели платы (артикул/ревизия) — для журнала и отчётов.
    board_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    golden_board_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("golden_board_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    golden_alignment_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    alignment_mae_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    alignment_mae_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ручная проверка оператора: дата, когда оператор прошёлся по всем дефектам
    # и подтвердил/отклонил каждый. До проверки поле None.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Относительный путь внутри storage/ к каталогу с артефактами для
    # дообучения модели (заполняется после ручной проверки).
    training_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )

    operator: Mapped["User"] = relationship(  # noqa: F821
        back_populates="inspections",
        foreign_keys=[operator_id],
    )
    device: Mapped["Device | None"] = relationship(  # noqa: F821
        foreign_keys=[device_id]
    )
    defects: Mapped[list["Defect"]] = relationship(
        back_populates="inspection",
        cascade="all,delete-orphan",
    )


class Defect(Base):
    """Отдельно обнаруженный дефект в рамках одной инспекции.

    Координаты ограничивающего прямоугольника хранятся в абсолютных пикселях
    исходного изображения (верхний левый и нижний правый углы).
    """

    __tablename__ = "defects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )

    class_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    class_name: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_x1: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y1: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_x2: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y2: Mapped[int] = mapped_column(Integer, nullable=False)

    # Оператор просмотрел дефект и выставил оценку? По умолчанию — False
    # (модель нашла дефект, но человек его ещё не подтверждал).
    is_reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Оценка оператора: True — реальный дефект, False — ложное срабатывание
    # модели (не брак). Имеет смысл только при is_reviewed=True.
    is_real_defect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Для дообучения: True — не включать бокс в labels.txt (ошибочный класс модели).
    exclude_from_training: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    inspection: Mapped[Inspection] = relationship(back_populates="defects")
