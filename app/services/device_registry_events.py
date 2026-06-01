"""Уведомления об изменении списка устройств / закреплений (SSE для всех клиентов)."""

from __future__ import annotations

import asyncio

_lock = asyncio.Lock()
_subscribers: set[asyncio.Queue[None]] = set()


async def subscribe_registry() -> asyncio.Queue[None]:
    q: asyncio.Queue[None] = asyncio.Queue(maxsize=16)
    async with _lock:
        _subscribers.add(q)
    return q


async def unsubscribe_registry(q: asyncio.Queue[None]) -> None:
    async with _lock:
        _subscribers.discard(q)


async def notify_device_registry_changed() -> None:
    """Вызывать после take/release/CRUD устройств."""
    async with _lock:
        queues = list(_subscribers)
    for q in queues:
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
