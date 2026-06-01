"""Долгоживущие HTTP-потоки (SSE, MJPEG) без ложного ERROR в uvicorn при отмене.

Starlette ``StreamingResponse`` при закрытии вкладки, Ctrl+C или ``--reload`` часто
завершает задачу через ``asyncio.CancelledError``. Uvicorn логирует это как
«Exception in ASGI application». Для потоков, где отмена — норма, глотаем только
дерево отмен и завершаем ответ без проброса наружу.
"""

from __future__ import annotations

import asyncio
import builtins

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


def is_async_cancel_tree(exc: BaseException) -> bool:
    """True, если исключение — только отмена asyncio (в т.ч. вложенная ExceptionGroup)."""
    if isinstance(exc, asyncio.CancelledError):
        return True
    beg = getattr(builtins, "BaseExceptionGroup", None)
    if beg is not None and isinstance(exc, beg):
        return all(is_async_cancel_tree(e) for e in exc.exceptions)
    return False


def is_uvicorn_worker_shutdown_noise(exc: BaseException | None, *, _depth: int = 0) -> bool:
    """Отмена задач / Ctrl+C / --reload: не считаем за ошибку приложения в логах uvicorn."""
    if exc is None or _depth > 28:
        return False
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        return True
    beg = getattr(builtins, "BaseExceptionGroup", None)
    if beg is not None and isinstance(exc, beg):
        return all(is_uvicorn_worker_shutdown_noise(e, _depth=_depth + 1) for e in exc.exceptions)
    cause = getattr(exc, "__cause__", None)
    if cause is not None and is_uvicorn_worker_shutdown_noise(cause, _depth=_depth + 1):
        return True
    return False


class LongLivedStreamingResponse(StreamingResponse):
    """Как ``StreamingResponse``, но без проброса ``CancelledError`` наружу ASGI."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except BaseException as exc:
            if is_async_cancel_tree(exc):
                return
            raise
