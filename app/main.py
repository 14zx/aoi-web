"""Точка входа FastAPI-приложения «АОИ-Web».

Запуск сервера разработки::

    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import (
    audit_router,
    auth_router,
    datasets_router,
    devices_router,
    golden_boards_router,
    inspections_router,
    meta_router,
    pipeline_router,
    settings_router,
    stats_router,
    users_router,
)
from .config import BASE_DIR, settings
from .core.logging import configure_logging, get_logger
from .core.schema_upgrade import upgrade_schema
from .database import Base, engine


configure_logging()
logger = get_logger(__name__)


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Не спамить ERROR при обрыве TLS/HTTP клиентом (типично для Windows + localhost)."""
    exc = context.get("exception")
    if isinstance(exc, ConnectionResetError):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения: создание таблиц, прогрев детектора."""
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_asyncio_exception_handler)
    except RuntimeError:
        pass
    from .config import effective_public_base_url

    pub = effective_public_base_url()
    if pub:
        logger.info("PUBLIC_BASE_URL для телефона/QR: %s", pub)
    logger.info("Запуск %s %s", settings.app_name, __version__)
    Base.metadata.create_all(bind=engine)
    upgrade_schema(engine)

    # Отложенный импорт, чтобы избежать загрузки модели при сборке OpenAPI.
    from sqlalchemy import select

    from .database import SessionLocal
    from .models import User, UserRole
    from .services.detector import get_detector
    from .services import dataset_manager

    detector = get_detector()
    # Если в БД уже есть активный датасет — подгрузим его поверх конфига.
    with SessionLocal() as db:
        user = db.execute(
            select(User).where(User.username == settings.admin_username)
        ).scalar_one_or_none()
        if user is not None and user.role == UserRole.MANAGER:
            user.role = UserRole.ADMIN
            db.commit()
            logger.info(
                "Учётная запись %s: роль обновлена manager → admin",
                settings.admin_username,
            )
        try:
            dataset_manager.ensure_detector_synced(db)
        except Exception as exc:  # pragma: no cover
            logger.warning("Не удалось применить активный датасет из БД: %s", exc)
    logger.info("Детектор инициализирован: backend=%s", detector.backend)
    yield
    logger.info("Завершение работы %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Программная часть ПАК автоматической оптической инспекции печатных узлов (АОИ-Web): "
        "инспекции, датасеты, учётные записи, пайплайн освещения и захвата, эталоны плат."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Маршруты API ----
app.include_router(meta_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(devices_router)
app.include_router(datasets_router)
app.include_router(settings_router)
app.include_router(inspections_router)
app.include_router(pipeline_router)
app.include_router(golden_boards_router)
app.include_router(stats_router)
app.include_router(audit_router)


# ---- Статика и SPA-индекс ----
# В PyInstaller-сборке статика лежит в ``web_static`` (см. spec), иначе каталог
# ``app/static`` на диске перекрывает пакет ``app`` и ломает ``import app.main``.
if getattr(sys, "frozen", False):
    STATIC_DIR = Path(BASE_DIR) / "web_static"
else:
    STATIC_DIR = Path(BASE_DIR) / "app" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Отдаёт клиентскую SPA-страницу оператора/руководителя."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(
            {"detail": "Клиентская часть не найдена. Соберите статические файлы."},
            status_code=500,
        )
    return FileResponse(index_file)


@app.get("/phone", include_in_schema=False)
def phone_page() -> FileResponse:
    """Отдаёт минималистичную страницу «телефон-камера».

    Используется совместно со ссылкой ``/phone?device=<id>&token=<upload_token>``,
    сгенерированной из админки. JWT не требуется.
    """
    phone_file = STATIC_DIR / "phone.html"
    if not phone_file.exists():
        return JSONResponse({"detail": "phone.html не найден"}, status_code=500)
    return FileResponse(phone_file)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Отдаёт favicon (если он есть в статике) или пустой 204-ответ.

    Важно: для статуса 204 No Content тело должно быть пустым (RFC 7230),
    иначе uvicorn падает с ``Response content longer than Content-Length``.
    Поэтому здесь используется голый ``Response(status_code=204)``,
    а не ``JSONResponse``.
    """
    icon = STATIC_DIR / "favicon.ico"
    if icon.exists():
        return FileResponse(icon, media_type="image/x-icon")
    return Response(status_code=status.HTTP_204_NO_CONTENT)