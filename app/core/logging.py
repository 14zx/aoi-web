"""Централизованная настройка логирования.

Уровни детализации INFO/WARNING/ERROR/CRITICAL — как требует ТЗ п. 4.2.
Логи пишутся одновременно в stdout и в файл.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from ..config import settings


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging() -> None:
    """Настраивает корневой логгер приложения."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    # Предотвращаем повторное добавление обработчиков при повторном импорте.
    if getattr(configure_logging, "_configured", False):
        return

    formatter = logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Подавляем излишне подробные логи внешних библиотек.
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.getLogger("passlib").setLevel(logging.ERROR)

    # Uvicorn пишет ERROR «Exception in ASGI application» на каждый
    # asyncio.CancelledError при закрытии SSE/MJPEG или --reload — это шум.
    from .long_lived_stream import is_async_cancel_tree, is_uvicorn_worker_shutdown_noise

    class UvicornAsgiCancelFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.name != "uvicorn.error":
                return True
            msg = record.getMessage()
            if (
                isinstance(msg, str)
                and msg.lstrip().startswith("Traceback")
                and "asyncio.exceptions.CancelledError" in msg
                and "KeyboardInterrupt" in msg
                and "starlette\\routing.py" in msg
                and "lifespan" in msg
            ):
                return False
            exc_info = record.exc_info
            if not exc_info or exc_info[1] is None:
                return True
            exc = exc_info[1]
            if is_async_cancel_tree(exc) or is_uvicorn_worker_shutdown_noise(exc):
                record.levelno = logging.DEBUG
                record.levelname = "DEBUG"
                if isinstance(record.msg, str):
                    if "Exception in ASGI application" in record.msg:
                        record.msg = "ASGI task cancelled (disconnect or server reload)."
                    elif "lifespan" in record.msg and "protocol" in record.msg:
                        record.msg = "Lifespan task cancelled (reload or disconnect)."
                    elif record.msg.strip().startswith("Traceback"):
                        record.msg = "Worker shutdown/reload (async cancellation)."
                # exc_text выставляется при создании LogRecord — без сброса трассировка
                # всё равно попадёт в DefaultFormatter.
                record.exc_info = None
                record.exc_text = None
            return True

    logging.getLogger("uvicorn.error").addFilter(UvicornAsgiCancelFilter())

    configure_logging._configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """Возвращает именованный логгер."""
    return logging.getLogger(name)
