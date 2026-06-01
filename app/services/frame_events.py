"""Уведомления для SSE /frame-events: кадры и снимок статуса устройства.

Работает в памяти процесса uvicorn; при нескольких воркерах подписка действует
только на том воркере, куда попали POST /frame и POST /status от телефона.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, DefaultDict, Set

_lock = asyncio.Lock()
_subscribers: DefaultDict[int, Set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)


async def subscribe(device_id: int) -> asyncio.Queue[dict[str, Any]]:
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
    async with _lock:
        _subscribers[device_id].add(q)
    return q


async def unsubscribe(device_id: int, q: asyncio.Queue[dict[str, Any]]) -> None:
    async with _lock:
        _subscribers[device_id].discard(q)
        if device_id in _subscribers and not _subscribers[device_id]:
            del _subscribers[device_id]


async def _notify(device_id: int, msg: dict[str, Any]) -> None:
    async with _lock:
        queues = list(_subscribers.get(device_id, ()))
    for q in queues:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


async def publish_frame_ready(device_id: int) -> None:
    """После успешного stream_store.put."""
    await _notify(device_id, {"kind": "frame"})


async def publish_device_status(device_id: int, status: dict[str, Any]) -> None:
    """Полный JSON DeviceStatusOut — после POST /status от телефона или кадра."""
    await _notify(device_id, {"kind": "status", "data": status})
