"""Pydantic-схемы для редактируемых в админке настроек."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SettingsOut(BaseModel):
    """Текущие значения управляемых из UI параметров."""

    detection_conf_threshold: float
    detection_iou_threshold: float
    live_analysis_interval_ms: int
    live_analysis_max_side: int


class SettingsUpdate(BaseModel):
    """Запрос на обновление. Все поля необязательны."""

    detection_conf_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    detection_iou_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    live_analysis_interval_ms: int | None = Field(default=None, ge=200, le=10000)
    live_analysis_max_side: int | None = Field(default=None, ge=320, le=1920)
