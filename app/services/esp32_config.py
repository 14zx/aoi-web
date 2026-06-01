"""Настройки WLED в БД (вкладка «Инспекция»)."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings as env_settings
from ..models import AppSetting
from .esp32_color_map import resolve_color_map

logger = logging.getLogger(__name__)

SETTING_KEY = "wled_hardware"
LEGACY_SETTING_KEY = "esp32_hardware"


@dataclass
class Esp32Config:
    enabled: bool = False
    base_url: str = ""
    connection_mode: str = "manual"  # manual | auto
    health_path: str = "/json/info"
    control_path: str = "/json/state"
    segment_id: int = 0
    transition: int = 7
    color_order: str = "rgb"  # rgb | swap_gb | brg | custom
    color_map: str = "rgb"  # перестановка слотов API, см. esp32_color_map
    color_cal: dict[str, str] | None = None  # r/g/b → что видно на ленте при чистом канале
    timeout_sec: float = 2.5
    cache_sec: float = 3.0

    def normalized(self) -> Esp32Config:
        base = (self.base_url or "").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            base = f"http://{base}"
        hp = (self.health_path or "/json/info").strip()
        if not hp.startswith("/"):
            hp = "/" + hp
        cp = (self.control_path or "/json/state").strip()
        if not cp.startswith("/"):
            cp = "/" + cp
        mode = str(self.connection_mode or "manual").strip().lower()
        if mode not in ("manual", "auto"):
            mode = "manual"
        co = str(self.color_order or "rgb").strip().lower()
        if co not in ("rgb", "swap_gb", "brg", "custom"):
            co = "rgb"
        cm = resolve_color_map(co, self.color_map)
        cal = self.color_cal
        if cal is not None and not isinstance(cal, dict):
            cal = None
        if cal is not None:
            cal = {k: str(v).lower() for k, v in cal.items() if k in "rgb" and str(v).lower() in "rgb"}
            if len(cal) != 3:
                cal = None
        return Esp32Config(
            enabled=bool(self.enabled),
            base_url=base,
            connection_mode=mode,
            health_path=hp,
            control_path=cp,
            segment_id=max(0, int(self.segment_id)),
            transition=max(0, min(65535, int(self.transition))),
            color_order=co,
            color_map=cm,
            color_cal=cal,
            timeout_sec=max(0.3, min(30.0, float(self.timeout_sec))),
            cache_sec=max(0.0, min(60.0, float(self.cache_sec))),
        )

    def is_usable(self) -> bool:
        n = self.normalized()
        return n.enabled and bool(n.base_url)


def _defaults_from_env() -> Esp32Config:
    base = ""
    if env_settings.esp32_base_url:
        base = env_settings.esp32_base_url.strip()
    enabled = env_settings.hardware_transport == "http" and bool(base)
    hp = env_settings.esp32_health_path
    if hp in ("/health", ""):
        hp = "/json/info"
    cp = env_settings.esp32_preset_path
    if cp.endswith("/preset") or cp.endswith("/control"):
        cp = "/json/state"
    return Esp32Config(
        enabled=enabled,
        base_url=base,
        health_path=hp,
        control_path=cp,
        timeout_sec=env_settings.esp32_request_timeout_sec,
        cache_sec=env_settings.esp32_status_cache_sec,
    )


def _migrate_legacy_paths(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    hp = str(data.get("health_path") or data.get("info_path") or "/json/info")
    if hp == "/health":
        hp = "/json/info"
    data["health_path"] = hp
    cp = str(data.get("control_path") or data.get("state_path") or "/json/state")
    if cp.endswith("/preset") or cp.endswith("/control"):
        cp = "/json/state"
    data["control_path"] = cp
    return data


def _from_dict(raw: dict[str, Any]) -> Esp32Config:
    raw = _migrate_legacy_paths(raw)
    return Esp32Config(
        enabled=bool(raw.get("enabled", False)),
        base_url=str(raw.get("base_url") or ""),
        connection_mode=str(raw.get("connection_mode") or "manual"),
        health_path=str(raw.get("health_path") or "/json/info"),
        control_path=str(raw.get("control_path") or "/json/state"),
        segment_id=int(raw.get("segment_id", 0)),
        transition=int(raw.get("transition", 7)),
        color_order=str(raw.get("color_order", "rgb")),
        color_map=str(raw.get("color_map", "rgb")),
        color_cal=raw.get("color_cal") if isinstance(raw.get("color_cal"), dict) else None,
        timeout_sec=float(raw.get("timeout_sec", 2.5)),
        cache_sec=float(raw.get("cache_sec", 3.0)),
    ).normalized()


def _load_row(db: Session) -> AppSetting | None:
    row = db.get(AppSetting, SETTING_KEY)
    if row is None:
        row = db.get(AppSetting, LEGACY_SETTING_KEY)
    return row


class Esp32ConfigService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: Esp32Config | None = None

    def load(self, db: Session) -> Esp32Config:
        with self._lock:
            if self._cache is not None:
                return self._cache
        row = _load_row(db)
        if row is None or not row.value:
            cfg = _defaults_from_env().normalized()
        else:
            try:
                parsed = json.loads(row.value)
                if not isinstance(parsed, dict):
                    cfg = _defaults_from_env().normalized()
                else:
                    cfg = _from_dict(parsed)
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("wled_config: invalid JSON, using defaults")
                cfg = _defaults_from_env().normalized()
        with self._lock:
            self._cache = cfg
        return cfg

    def save(
        self,
        db: Session,
        cfg: Esp32Config,
        *,
        updated_by: int | None = None,
    ) -> Esp32Config:
        normalized = cfg.normalized()
        payload = json.dumps(asdict(normalized), ensure_ascii=False)
        existing = db.get(AppSetting, SETTING_KEY)
        if existing is None:
            db.add(
                AppSetting(
                    key=SETTING_KEY,
                    value=payload,
                    updated_by=updated_by,
                )
            )
        else:
            existing.value = payload
            existing.updated_by = updated_by
        legacy = db.get(AppSetting, LEGACY_SETTING_KEY)
        if legacy is not None:
            db.delete(legacy)
        db.commit()
        with self._lock:
            self._cache = normalized
        return normalized

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None


esp32_config_service = Esp32ConfigService()
