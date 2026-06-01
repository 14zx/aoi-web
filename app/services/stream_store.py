"""Потокобезопасное хранилище «последнего кадра» по устройствам.

Устройство (телефон с камерой) публикует кадры через
``POST /api/devices/{id}/frame``. Последний принятый кадр хранится в памяти
процесса и отдаётся сколько угодно клиентам через
``GET /api/devices/{id}/frame.jpg``.

Для промышленных развёртываний рекомендуется заменить на Redis/RTSP, но для
целей диплома этого более чем достаточно: сервер и клиенты в одной LAN,
задержка порядка 200–500 мс при частоте 2–4 к/с.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass
class StoredFrame:
    data: bytes
    content_type: str
    received_at: datetime


class StreamStore:
    """In-memory last-frame per device_id."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._frames: dict[int, StoredFrame] = {}

    def put(self, device_id: int, data: bytes, content_type: str = "image/jpeg") -> StoredFrame:
        frame = StoredFrame(data=data, content_type=content_type, received_at=datetime.utcnow())
        with self._lock:
            self._frames[device_id] = frame
        return frame

    def get(self, device_id: int) -> StoredFrame | None:
        with self._lock:
            return self._frames.get(device_id)

    def drop(self, device_id: int) -> None:
        with self._lock:
            self._frames.pop(device_id, None)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()


stream_store = StreamStore()
