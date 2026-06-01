"""Pydantic-схемы устройств."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    identifier: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    identifier: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    designated_operator_id: int | None = Field(
        default=None,
        description="ID сотрудника, за которым закреплена камера; null — снять закрепление",
    )


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    identifier: str | None
    description: str | None
    is_active: bool
    assigned_operator_id: int | None
    assigned_operator_username: str | None = None
    assigned_at: datetime | None
    designated_operator_id: int | None = None
    designated_operator_username: str | None = None
    registered_by_id: int | None = None
    registered_by_username: str | None = None
    last_seen_at: datetime | None = None
    is_streaming: bool = False
    created_at: datetime

    # Поля, раскрываемые только при создании/регенерации/запросе ссылки.
    upload_token: str | None = None
    streaming_link: str | None = None
    # Признак наличия токена (без раскрытия самого значения).
    has_upload_token: bool = False


class DeviceCommand(BaseModel):
    """Команда от PC к телефону."""

    command: str = Field(min_length=1, max_length=32)
    value: str | None = Field(default=None, max_length=32)


class DeviceStatusIn(BaseModel):
    """Статус, который телефон сообщает о себе."""

    is_streaming: bool | None = None
    preset: str | None = Field(default=None, max_length=16)
    torch_on: bool | None = None
    facing: str | None = Field(default=None, max_length=16)


class DeviceStatusOut(BaseModel):
    """Сводный статус устройства для PC-UI."""

    is_streaming: bool = False
    preset: str | None = None
    torch_on: bool = False
    facing: str | None = None
    # Таймстамп последнего обновления. Позволяет PC понять, «живой» ли телефон.
    updated_at: datetime | None = None
    # Последний кадр (по stream_store) — дополнительно для удобства.
    frame_received_at: datetime | None = None
    # Явно: в памяти этого процесса есть JPEG для GET .../frame.jpg
    frame_available: bool = False
