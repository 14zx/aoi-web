"""Лёгкое авто-обновление схемы для SQLite без полноценной alembic-миграции.

Проверяет, что в таблицах существуют все необходимые колонки, и выполняет
``ALTER TABLE ADD COLUMN`` для недостающих. Выполняется при старте приложения
после ``Base.metadata.create_all``. Для продакшн-конфигурации PostgreSQL
следует использовать миграции alembic (каталог ``alembic/versions``).
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


logger = logging.getLogger(__name__)


# (таблица, имя колонки, определение DDL для ADD COLUMN).
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("inspections", "device_id", "INTEGER"),
    ("inspections", "conf_threshold", "FLOAT"),
    ("inspections", "reviewed_at", "DATETIME"),
    ("inspections", "training_dir", "VARCHAR(255)"),
    ("inspections", "board_model", "VARCHAR(255)"),
    ("inspections", "golden_board_profile_id", "INTEGER"),
    ("inspections", "golden_alignment_used", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("inspections", "alignment_mae_before", "FLOAT"),
    ("inspections", "alignment_mae_after", "FLOAT"),
    ("defects", "is_reviewed", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("defects", "is_real_defect", "BOOLEAN DEFAULT 1 NOT NULL"),
    ("defects", "exclude_from_training", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("defects", "polygon", "TEXT"),
    ("devices", "upload_token", "VARCHAR(64)"),
    ("devices", "registered_by_id", "INTEGER"),
    ("devices", "last_seen_at", "DATETIME"),
    ("devices", "designated_operator_id", "INTEGER"),
    ("golden_board_profiles", "designated_operator_id", "INTEGER"),
]


def upgrade_schema(engine: Engine) -> None:
    """Добавляет отсутствующие колонки."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, column, ddl in _COLUMN_MIGRATIONS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if column in cols:
                continue
            logger.info("schema_upgrade: %s.%s добавляется (%s)", table, column, ddl)
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
