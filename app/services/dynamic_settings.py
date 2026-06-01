"""Сервис редактируемых в работающей системе настроек.

Хранилище — таблица ``app_settings`` (ключ/значение). Значения кешируются в
памяти процесса; кэш сбрасывается при любом изменении.

Значения по умолчанию берутся из ``app.config.settings`` (а те — из ``.env``).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings as env_settings
from ..models import AppSetting


T = TypeVar("T")


# Допустимые ключи и их типы + значения по умолчанию.
_DEFAULTS: dict[str, tuple[type, Any]] = {
    "detection_conf_threshold": (float, env_settings.detection_conf_threshold),
    "detection_iou_threshold": (float, env_settings.detection_iou_threshold),
    "live_analysis_interval_ms": (int, 1200),
    "live_analysis_max_side": (int, 640),
}


def _parse(value: str, kind: type) -> Any:
    if kind is float:
        return float(value)
    if kind is int:
        return int(float(value))
    if kind is bool:
        return value.lower() in ("1", "true", "yes", "on")
    return value


class DynamicSettingsService:
    """Потокобезопасный кэш редактируемых настроек."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}
        self._loaded: bool = False

    # ------------------------------------------------------------
    def _ensure_loaded(self, db: Session) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            rows = db.execute(select(AppSetting)).scalars().all()
            data: dict[str, Any] = {
                k: v for k, (_, v) in _DEFAULTS.items()
            }
            for row in rows:
                if row.key not in _DEFAULTS:
                    continue
                kind, _ = _DEFAULTS[row.key]
                try:
                    data[row.key] = _parse(row.value, kind)
                except (TypeError, ValueError):
                    continue
            self._cache = data
            self._loaded = True

    # ------------------------------------------------------------
    def all(self, db: Session) -> dict[str, Any]:
        self._ensure_loaded(db)
        with self._lock:
            return dict(self._cache)

    def get(self, db: Session, key: str) -> Any:
        self._ensure_loaded(db)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        if key in _DEFAULTS:
            return _DEFAULTS[key][1]
        raise KeyError(key)

    # ------------------------------------------------------------
    def update(
        self,
        db: Session,
        *,
        values: dict[str, Any],
        updated_by: int | None = None,
    ) -> dict[str, Any]:
        """Обновляет указанные ключи. Коммитит транзакцию."""
        self._ensure_loaded(db)
        for key, value in values.items():
            if value is None:
                continue
            if key not in _DEFAULTS:
                raise KeyError(f"Неизвестный параметр: {key}")
            existing = db.get(AppSetting, key)
            if existing is None:
                db.add(AppSetting(key=key, value=str(value), updated_by=updated_by))
            else:
                existing.value = str(value)
                existing.updated_by = updated_by
        db.commit()
        with self._lock:
            self._loaded = False
            self._cache.clear()
        return self.all(db)


dynamic_settings = DynamicSettingsService()


def get_conf_threshold(db: Session) -> float:
    return float(dynamic_settings.get(db, "detection_conf_threshold"))


def get_iou_threshold(db: Session) -> float:
    return float(dynamic_settings.get(db, "detection_iou_threshold"))
