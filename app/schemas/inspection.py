"""Pydantic-схемы инспекций и статистики."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models.inspection import InspectionStatus


class DefectOut(BaseModel):
    """Информация об одном обнаруженном объекте (дефект, компонент и т.д.)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    class_code: str
    class_name: str
    confidence: float
    bbox_x1: int
    bbox_y1: int
    bbox_x2: int
    bbox_y2: int
    # Контур сегментации [[x, y], ...] для обводки «пиксель-в-пиксель»; None — bbox.
    polygon: list[list[int]] | None = None
    is_reviewed: bool = False
    is_real_defect: bool = True
    semantic_kind: Literal["defect", "component", "ignore"] = "defect"
    exclude_from_training: bool = False


class DefectReviewIn(BaseModel):
    """Оценка одного дефекта оператором при ручной проверке."""

    defect_id: int
    is_real_defect: bool
    exclude_from_training: bool = False


class InspectionReviewIn(BaseModel):
    """Результаты ручной проверки инспекции (Ф2 «оценка оператором»)."""

    reviews: list[DefectReviewIn]


CONFIRM_PURGE_ALL_INSPECTIONS = "DELETE_ALL_INSPECTIONS"


class PurgeAllInspectionsIn(BaseModel):
    """Подтверждение необратимого удаления журнала на сервере."""

    confirm: str = Field(
        ...,
        min_length=1,
        description=f"Строка должна быть в точности: {CONFIRM_PURGE_ALL_INSPECTIONS}",
    )


class PurgeAllInspectionsOut(BaseModel):
    deleted: int


class InspectionListItem(BaseModel):
    """Элемент журнала инспекций."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    operator_id: int
    operator_username: str | None = None
    device_id: int | None = None
    device_name: str | None = None
    original_filename: str
    board_model: str | None = None
    golden_board_profile_id: int | None = None
    golden_alignment_used: bool = False
    status: InspectionStatus
    defects_count: int
    avg_confidence: float | None
    inference_time_ms: float | None
    created_at: datetime


class InspectionDetailOut(BaseModel):
    """Полные сведения об инспекции."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    operator_id: int
    operator_username: str | None = None
    device_id: int | None = None
    device_name: str | None = None
    original_filename: str
    board_model: str | None = None
    golden_board_profile_id: int | None = None
    golden_alignment_used: bool = False
    alignment_mae_before: float | None = None
    alignment_mae_after: float | None = None
    original_url: str
    result_url: str | None
    image_width: int | None
    image_height: int | None
    status: InspectionStatus
    defects_count: int
    detections_count: int
    avg_confidence: float | None
    inference_time_ms: float | None
    conf_threshold: float | None
    notes: str | None
    error_message: str | None
    training_dir: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    defects: list[DefectOut]


class LiveDetectionResult(BaseModel):
    """Результат быстрого анализа одного кадра live-потока."""

    image_width: int
    image_height: int
    inference_time_ms: float
    backend: str
    conf_threshold: float
    detections_count: int
    semantic_defect_count: int
    golden_board_profile_id: int | None = None
    golden_alignment_used: bool = False
    alignment_mae_before: float | None = None
    alignment_mae_after: float | None = None
    defects: list[DefectOut]


class DefectClassStat(BaseModel):
    """Статистика по классу дефекта."""

    class_code: str
    class_name: str
    count: int


class OperatorStats(BaseModel):
    """Статистика по одному оператору."""

    operator_id: int
    username: str
    full_name: str
    inspections_count: int
    defects_count: int


class DailyStat(BaseModel):
    """Значение на одну дату (по умолчанию — UTC-day)."""

    date: str  # ISO YYYY-MM-DD
    inspections: int
    defects: int


class WeekdayStat(BaseModel):
    """Среднее/суммарное за день недели в диапазоне."""

    weekday: int            # 0 — понедельник
    weekday_name: str       # «Пн», «Вт», …
    inspections: int
    defects: int


class InspectionStatsOut(BaseModel):
    """Агрегированная статистика для руководителя (ТЗ Ф9)."""

    total_inspections: int
    total_defects: int
    defective_inspections: int
    clean_inspections: int
    by_class: list[DefectClassStat]
    by_operator: list[OperatorStats]
    by_day: list[DailyStat] = []
    by_weekday: list[WeekdayStat] = []
    from_date: datetime | None = None
    to_date: datetime | None = None
