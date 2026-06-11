"""Конфигурация приложения.

Настройки загружаются из переменных окружения или файла ``.env``.
Центральный источник истины для всех параметров, допускающих настройку —
соответствует требованиям ТЗ к эксплуатационной документации.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Параметры приложения, загружаемые из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Отключаем защищённый namespace "model_", чтобы не конфликтовать с
        # параметрами модели детекции (model_weights_path и др.).
        protected_namespaces=(),
    )

    # ---- Общие ----
    app_name: str = "АОИ-Web"
    app_code: str = "АОИ.01"
    app_version: str = "1.0.3"
    debug: bool = False

    # ---- Безопасность (ТЗ п. 4.8.1) ----
    secret_key: str = Field(
        default="dev-secret-change-me-please-change-me-please",
        description="Секрет для подписи JWT-токенов",
    )
    algorithm: str = "HS256"
    # ТЗ: срок действия JWT-токена — не более 8 часов (480 мин).
    access_token_expire_minutes: int = 480
    bcrypt_rounds: int = 12
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15

    # ---- База данных ----
    database_url: str = f"sqlite:///{BASE_DIR / 'aoi.db'}"

    # ---- Хранилище ----
    storage_dir: Path = BASE_DIR / "storage"
    max_upload_mb: int = 15

    # ---- Модель детекции (ТЗ п. 4.1.3) ----
    model_weights_path: Path = BASE_DIR / "models" / "datasets" / "7" / "weights.pt"
    detection_conf_threshold: float = 0.25
    detection_iou_threshold: float = 0.45
    detection_img_size: int = 640
    # Анализ всей платы одним кадром (рекомендуется): YOLO сам делает letterbox
    # до ``detection_full_image_imgsz``, контекст платы не теряется. Тайлинг
    # (дробление на куски) включайте только если мелкие дефекты теряются —
    # он рвёт компоненты на стыках плиток и портит распознавание.
    detection_tiling_enabled: bool = False
    # imgsz для анализа всего кадра. Чем больше — тем лучше видно мелкие
    # компоненты на крупном фото (ценой скорости). Кратно 32.
    detection_full_image_imgsz: int = Field(default=1280, ge=320, le=4096)
    # Тайловый инференс на крупных снимках (может повысить полноту поиска мелких объектов).
    detection_tiling_min_side: int = 1280
    detection_tile_size: int = 640
    detection_tile_overlap: float = 0.2
    # ---- Предобработка кадра перед детекцией (выключено по умолчанию) ----
    # Включайте после настройки под камеру; порядок этапов в коде:
    # устранение дисторсии → шум → выравнивание освещения (CLAHE или top-hat по L в Lab).
    detection_preprocess_enabled: bool = False
    # JSON с калибровкой OpenCV: camera_matrix (3×3), dist_coeffs (плоский список или вложенный).
    # Если файл отсутствует или preprocess_lens_undistort=false — этап пропускается.
    detection_calibration_json: Path | None = None
    preprocess_lens_undistort: bool = False
    preprocess_noise_filter: Literal["none", "bilateral", "median"] = "none"
    preprocess_bilateral_d: int = Field(default=7, ge=1, le=15)
    preprocess_bilateral_sigma_color: float = Field(default=75.0, ge=1.0, le=255.0)
    preprocess_bilateral_sigma_space: float = Field(default=75.0, ge=1.0, le=255.0)
    preprocess_median_ksize: int = Field(default=3, ge=3, le=9)  # только нечётные; при чётном поднимется до ближайшего нечётного
    preprocess_illumination: Literal["none", "clahe", "tophat_lab"] = "none"
    preprocess_clahe_clip_limit: float = Field(default=2.0, ge=1.0, le=8.0)
    preprocess_clahe_tile_grid: int = Field(default=8, ge=2, le=32)
    # Ядро morp opening для top-hat по каналу L (Lab); сглаживание фона / виньетирование.
    preprocess_tophat_kernel_size: int = Field(default=31, ge=9, le=127)

    # ---- Аппаратный шлюз и ECC-выравнивание (ТЗ п. 3, модернизация) ----
    hardware_transport: Literal["mock", "serial", "http"] = "mock"
    # ESP32 в LAN (при hardware_transport=http). Пример: http://192.168.0.50
    esp32_base_url: str | None = None
    esp32_health_path: str = "/health"
    esp32_preset_path: str = "/api/lighting/preset"
    esp32_request_timeout_sec: float = Field(default=2.5, ge=0.3, le=30.0)
    esp32_status_cache_sec: float = Field(default=3.0, ge=0.0, le=60.0)
    alignment_ecc_max_iters: int = Field(default=120, ge=20, le=5000)
    alignment_ecc_motion: Literal["affine", "euclidean"] = "affine"
    # Сверка YOLO с regions Golden Board после ECC (эталонные зоны компонентов).
    golden_region_check_enabled: bool = True
    golden_region_min_iou: float = Field(default=0.2, ge=0.05, le=0.95)
    # Расширение зоны эталона (px) при сверке с детекциями — допуск смещения bbox.
    golden_region_tolerance_px: int = Field(default=12, ge=0, le=128)
    # Сверка regions/полярности, если ECC не улучшил кадр (только resize к размеру эталона).
    golden_compare_without_ecc: bool = True
    golden_polarity_check_enabled: bool = True
    golden_polarity_max_pixel_mae: float = Field(default=24.0, ge=4.0, le=128.0)
    golden_polarity_stripe_min_delta: float = Field(default=8.0, ge=2.0, le=64.0)

    # Порог отклонения ориентации компонента (градусы) — превышение → запись как дефект.
    component_tilt_max_deg: float = 25.0

    # ---- Поиск перемычек припоя (solder bridge) ----
    # Ищет нежелательный мостик припоя между двумя соседними компонентами/выводами.
    solder_bridge_check_enabled: bool = True
    # Максимальный зазор между компонентами (px), который ещё может быть перемкнут.
    solder_bridge_max_gap_px: int = Field(default=40, ge=2, le=512)
    # Припой — яркий и малонасыщенный (металлический блик): порог по V (яркость) и S (насыщенность) в HSV.
    solder_bridge_min_brightness: int = Field(default=170, ge=0, le=255)
    solder_bridge_max_saturation: int = Field(default=90, ge=0, le=255)
    # Доля «припойных» пикселей в зоне зазора, при которой считаем мостик подтверждённым.
    solder_bridge_min_fill: float = Field(default=0.45, ge=0.05, le=1.0)
    # Путь к расширенному реестру классов. Если файл существует и
    # ``use_unified_classes`` включено, детектор использует этот реестр
    # вместо зашитых 6 PCB-классов (PCB + пайка + монтаж компонентов).
    unified_classes_path: Path = BASE_DIR / "models" / "unified_classes.yaml"
    use_unified_classes: bool = False

    # ---- Логирование (ТЗ п. 4.2) ----
    log_file: Path = BASE_DIR / "logs" / "aoi.log"
    log_level: str = "INFO"

    # ---- Начальный администратор ----
    admin_username: str = "admin"
    admin_password: str = "admin12345"
    admin_full_name: str = "Администратор системы"

    # Базовый URL для ссылок на телефон (/phone?...). Задайте LAN-IP ПК, иначе
    # в ссылке будет localhost / 127.0.0.1 — с телефона не откроется.
    # Пример: PUBLIC_BASE_URL=https://192.168.1.5:8000
    public_base_url: str | None = None

    # ---- CORS ----
    allowed_origins: list[str] = ["*"]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает singleton-экземпляр настроек."""
    settings = Settings()
    # Гарантируем наличие каталогов для хранилища и логов.
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "originals").mkdir(parents=True, exist_ok=True)
    (settings.storage_dir / "results").mkdir(parents=True, exist_ok=True)
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    settings.model_weights_path.parent.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()


def effective_public_base_url() -> str | None:
    """URL для ссылок на телефон: env (лаунчер) → settings (.env)."""
    raw = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if raw:
        return raw.rstrip("/")
    if settings.public_base_url and str(settings.public_base_url).strip():
        return str(settings.public_base_url).strip().rstrip("/")
    return None
