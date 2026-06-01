"""Очередь команд «PC → телефон» для удалённого управления камерой.

Сценарий: оператор на странице инспекции жмёт кнопки (стоп/старт записи,
включение/выключение подсветки, выбор качества, переключение камеры), PC
посылает команду через ``POST /api/devices/{id}/control``, команда ставится
в очередь данного устройства. Телефон (страница ``/phone``) периодически
опрашивает ``GET /api/devices/{id}/commands`` и выполняет полученные команды.

Состояние (last_state) — это то, что телефон **регулярно сообщает** в
``POST /api/devices/{id}/status`` (является ли стрим активным, текущий
пресет качества, включена ли подсветка, фронт/тыл). PC читает его через тот
же эндпоинт, чтобы подсвечивать активные кнопки.

Реализация чисто in-memory — для диплома достаточно: всё в одном процессе
uvicorn, при рестарте очереди/статусы очищаются, что ожидаемо.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# Разрешённые команды и их допустимые значения. Всё, что не подходит под
# этот контракт, сервер отвергает с 400, чтобы не плодить произвольные
# сообщения на телефоне.
ALLOWED_COMMANDS: dict[str, set[str] | None] = {
    "start": None,
    "stop": None,
    "torch_on": None,
    "torch_off": None,
    "flip": None,
    "quality": {"sd", "hd", "fhd", "max"},
}


@dataclass
class Command:
    name: str
    value: str | None
    issued_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.name,
            "value": self.value,
            "issued_at": self.issued_at.isoformat(),
        }


@dataclass
class DeviceStatus:
    is_streaming: bool = False
    preset: str | None = None
    torch_on: bool = False
    facing: str | None = None  # "environment" | "user"
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_streaming": self.is_streaming,
            "preset": self.preset,
            "torch_on": self.torch_on,
            "facing": self.facing,
            "updated_at": self.updated_at.isoformat(),
        }


class CommandQueue:
    """Per-device FIFO очередь команд + последний известный статус телефона."""

    # Защита от неконтролируемого накопления при обрыве связи.
    MAX_PER_DEVICE = 32

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._queues: dict[int, deque[Command]] = {}
        self._status: dict[int, DeviceStatus] = {}

    # ------------------ команды ------------------
    def enqueue(self, device_id: int, name: str, value: str | None = None) -> Command:
        if name not in ALLOWED_COMMANDS:
            raise ValueError(f"Неизвестная команда: {name!r}")
        expected = ALLOWED_COMMANDS[name]
        if expected is not None:
            if value is None or value not in expected:
                raise ValueError(
                    f"Команда {name!r}: допустимые значения {sorted(expected)}"
                )
        cmd = Command(name=name, value=value)
        with self._lock:
            dq = self._queues.setdefault(device_id, deque())
            if len(dq) >= self.MAX_PER_DEVICE:
                dq.popleft()
            dq.append(cmd)
        return cmd

    def drain(self, device_id: int) -> list[Command]:
        with self._lock:
            dq = self._queues.get(device_id)
            if not dq:
                return []
            out = list(dq)
            dq.clear()
            return out

    def clear(self, device_id: int | None = None) -> None:
        with self._lock:
            if device_id is None:
                self._queues.clear()
                self._status.clear()
            else:
                self._queues.pop(device_id, None)
                self._status.pop(device_id, None)

    # ------------------ статус ------------------
    def set_status(
        self,
        device_id: int,
        *,
        is_streaming: bool | None = None,
        preset: str | None = None,
        torch_on: bool | None = None,
        facing: str | None = None,
    ) -> DeviceStatus:
        with self._lock:
            cur = self._status.get(device_id) or DeviceStatus()
            if is_streaming is not None:
                cur.is_streaming = bool(is_streaming)
            if preset is not None:
                cur.preset = preset
            if torch_on is not None:
                cur.torch_on = bool(torch_on)
            if facing is not None:
                cur.facing = facing
            cur.updated_at = datetime.utcnow()
            self._status[device_id] = cur
            return cur

    def get_status(self, device_id: int) -> DeviceStatus | None:
        with self._lock:
            return self._status.get(device_id)


command_queue = CommandQueue()
