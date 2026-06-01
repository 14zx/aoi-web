"""Инициализация базы данных и первичного администратора.

Запуск::

    python -m scripts.init_db              # создать схему + администратора
    python -m scripts.init_db --reset      # ПОЛНОСТЬЮ пересоздать схему (удаляет данные!)

Создаёт все таблицы и, если учётная запись администратора отсутствует, заводит
её по параметрам из ``.env`` (``ADMIN_USERNAME``, ``ADMIN_PASSWORD``) с ролью ``admin``.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.schema_upgrade import upgrade_schema
from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.models import User, UserRole


def main() -> int:
    parser = argparse.ArgumentParser(description="Инициализация БД АОИ-Web")
    parser.add_argument("--reset", action="store_true", help="Пересоздать схему (удаляет данные)")
    args = parser.parse_args()

    configure_logging()
    logger = get_logger(__name__)

    logger.info("Схема БД: %s", settings.database_url)
    if args.reset:
        logger.warning("--reset: удаление всех таблиц")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    upgrade_schema(engine)

    with SessionLocal() as db:
        existing = db.execute(
            select(User).where(User.username == settings.admin_username)
        ).scalar_one_or_none()
        if existing:
            logger.info("Администратор '%s' уже существует — пропускаем.", existing.username)
            return 0

        admin = User(
            username=settings.admin_username,
            full_name=settings.admin_full_name,
            hashed_password=hash_password(settings.admin_password),
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        logger.info(
            "Создан администратор '%s' с ролью 'admin'. "
            "Пароль задан переменной ADMIN_PASSWORD — обязательно смените его после первого входа.",
            admin.username,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
