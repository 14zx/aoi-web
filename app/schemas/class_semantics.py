"""Семантика классов модели (админка)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClassSemanticEntry(BaseModel):
    """Тип элемента на плате и нужна ли ручная оценка."""

    kind: Literal["component", "defect", "ignore"] = Field(default="defect")
    label: str = Field(default="", max_length=128)
    review_required: bool = True


class ClassSemanticsOut(BaseModel):
    """Классы активной модели и сохранённые назначения."""

    model_config = ConfigDict(protected_namespaces=())

    detector_classes: list[dict[str, str]]
    mappings: dict[str, ClassSemanticEntry]


class ClassSemanticsUpdate(BaseModel):
    mappings: dict[str, ClassSemanticEntry]
