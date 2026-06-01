"""Подключение к базе данных и управление сессиями SQLAlchemy."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def _build_engine():
    """Создаёт engine с учётом специфики SQLite (однопоточный по умолчанию)."""
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        future=True,
    )


engine = _build_engine()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    """Общий базовый класс для всех ORM-моделей."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI-зависимость: создаёт сессию на запрос и закрывает её по завершении."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
