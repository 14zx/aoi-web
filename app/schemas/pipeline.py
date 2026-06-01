"""Схемы API пайплайна АОИ (освещение, захват, демо ECC)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class Esp32HardwareConfigOut(BaseModel):
    enabled: bool
    base_url: str
    connection_mode: Literal["manual", "auto"] = "manual"
    health_path: str
    control_path: str
    segment_id: int = 0
    transition: int = 7
    color_order: Literal["rgb", "swap_gb", "brg", "custom"] = "rgb"
    color_map: str = "rgb"
    color_cal: dict[str, str] | None = None
    timeout_sec: float
    cache_sec: float


class Esp32HardwareConfigIn(BaseModel):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=256)
    connection_mode: Literal["manual", "auto"] | None = None
    health_path: str | None = Field(default=None, max_length=128)
    control_path: str | None = Field(default=None, max_length=128)
    segment_id: int | None = Field(default=None, ge=0, le=31)
    transition: int | None = Field(default=None, ge=0, le=65535)
    color_order: Literal["rgb", "swap_gb", "brg", "custom"] | None = None
    color_map: str | None = Field(default=None, min_length=3, max_length=3)
    color_cal: dict[str, str] | None = None
    timeout_sec: float | None = Field(default=None, ge=0.3, le=30.0)
    cache_sec: float | None = Field(default=None, ge=0.0, le=60.0)


class WledDiscoverIn(BaseModel):
    seed_base_url: str | None = Field(default=None, max_length=256)
    timeout_sec: float | None = Field(default=None, ge=0.5, le=15.0)
    use_mdns: bool = True
    use_nodes: bool = True


class WledDiscoveredDeviceOut(BaseModel):
    base_url: str
    ip: str = ""
    name: str = ""
    source: str = ""
    reachable: bool = False
    latency_ms: float | None = None
    message: str = ""
    info: dict[str, Any] | None = None


class WledDiscoverOut(BaseModel):
    devices: list[WledDiscoveredDeviceOut] = Field(default_factory=list)
    methods_used: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0


class WledDebugRequestIn(BaseModel):
    method: Literal["GET", "POST"] = "GET"
    path: str = Field(..., min_length=1, max_length=128)
    body: dict[str, Any] | None = None
    base_url: str | None = Field(default=None, max_length=256)

    @field_validator("path")
    @classmethod
    def _path_json(cls, v: str) -> str:
        p = v.strip()
        if not p.startswith("/"):
            p = "/" + p
        if not p.startswith("/json"):
            raise ValueError("Разрешены только пути JSON API WLED (/json/…)")
        return p


class WledHttpExchangeOut(BaseModel):
    ok: bool
    method: str
    url: str
    request_body: Any = None
    status_code: int | None = None
    response_body: Any = None
    latency_ms: float | None = None
    error: str | None = None


class WledAdminDiagnosticsOut(BaseModel):
    esp32_enabled: bool = False
    esp32_configured: bool = False
    esp32_base_url: str | None = None
    connection_mode: str = "manual"
    esp32_reachable: bool | None = None
    esp32_latency_ms: float | None = None
    esp32_probe_message: str | None = None
    esp32_device_info: dict[str, Any] | None = None
    last_wled_state: dict[str, Any] | None = None
    last_error: str | None = None
    recent_commands: list[str] = Field(default_factory=list)
    debug_exchanges: list[WledHttpExchangeOut] = Field(default_factory=list)


class LightingPresetIn(BaseModel):
    preset: Literal["white_diffuse", "rgb_highlight", "off"]


class LightingControlIn(BaseModel):
    """Управление подсветкой: пресет, яркость (0–100 %), цвет #RRGGBB."""

    preset: Literal["white_diffuse", "rgb_highlight", "off"] | None = None
    brightness: int | None = Field(default=None, ge=0, le=100)
    color: str | None = Field(default=None, max_length=7)

    @field_validator("color")
    @classmethod
    def _color_hex(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        s = str(v).strip()
        if not s.startswith("#"):
            s = f"#{s}"
        if not _COLOR_RE.match(s):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return s.lower()


class LightingControlOut(BaseModel):
    preset: str
    brightness: int
    color: str
    transport: str


class LightingPresetOut(LightingControlOut):
    """Алиас для обратной совместимости."""


class HardwareStatusOut(BaseModel):
    """Статус для оператора/руководителя (без отладочных полей)."""

    active_preset: str
    active_brightness: int = 80
    active_color: str = "#ffffff"
    last_capture_ack_ms: float | None
    commands_total: int
    last_error: str | None
    transport: str = "mock"
    esp32_enabled: bool = False
    esp32_configured: bool = False
    esp32_reachable: bool | None = None
    esp32_latency_ms: float | None = None
    esp32_probe_message: str | None = None


class CaptureAckOut(BaseModel):
    ok: bool = True


class AlignmentDemoOut(BaseModel):
    ok: bool
    mae_before: float
    mae_after: float
    message: str | None = None
