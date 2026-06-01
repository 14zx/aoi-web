"""Схемы Golden Board Manager."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class PolarityMarkerIn(BaseModel):
    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered_box(self) -> Self:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("Координаты маркера: требуется x2 > x1 и y2 > y1")
        return self


class GoldenBoardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    board_model: str | None = Field(default=None, max_length=255)
    payload: dict | list = Field(description="JSON эталона: рамки, классы и т.д.")


class GoldenBoardUpdate(BaseModel):
    designated_operator_id: int | None = Field(
        default=None,
        description="Закрепить эталон за сотрудником (null — снять закрепление)",
    )


class GoldenBoardOut(BaseModel):
    id: int
    name: str
    board_model: str | None
    author_id: int | None
    designated_operator_id: int | None = None
    designated_operator_username: str | None = None
    created_at: datetime
    has_reference_image: bool = False

    model_config = {"from_attributes": True}


class GoldenBoardChoiceOut(BaseModel):
    """Краткая запись для выбора эталона при инспекции."""

    id: int
    name: str
    board_model: str | None = None


class GoldenBoardDetailOut(GoldenBoardOut):
    payload: dict | list
    reference_image_url: str | None = None


class GoldenBoardRegionIn(BaseModel):
    """Прямоугольник разметки в координатах опорного снимка (пиксели)."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)
    label: str | None = Field(default=None, max_length=64)
    check_polarity: bool = Field(
        default=False,
        description="Проверять полярность/ориентацию маркера в зоне",
    )
    polarity_kind: Literal["electrolytic", "diode", "ic", "generic"] = Field(
        default="generic",
        description="Тип компонента для выбора способа сравнения маркера",
    )
    polarity_marker: PolarityMarkerIn | None = Field(
        default=None,
        description="Подзона маркера полярности (полоска катода, pin1 и т.д.)",
    )

    @model_validator(mode="after")
    def ordered_box(self) -> Self:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("Координаты: требуется x2 > x1 и y2 > y1")
        return self


class GoldenBoardMarkupIn(BaseModel):
    regions: list[GoldenBoardRegionIn] = Field(default_factory=list, max_length=500)
    region_tolerance_px: int | None = Field(
        default=None,
        ge=0,
        le=128,
        description="Допуск смещения bbox зоны эталона, px (по умолчанию 12)",
    )
