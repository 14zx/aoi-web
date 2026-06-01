"""Pydantic-схемы датасетов."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    file_size: int
    original_filename: str | None
    is_active: bool
    uploaded_by_id: int | None = None
    uploaded_by_username: str | None = None
    created_at: datetime
    updated_at: datetime
