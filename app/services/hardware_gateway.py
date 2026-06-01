"""Шлюз аппаратной синхронизации: подсветка WLED и сигнал захвата кадра.

Конфигурация WLED — в БД (``esp32_config`` / ``wled_hardware``), вкладка «Инспекция».
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock
from typing import Any, Literal

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class LightingPreset(StrEnum):
    """Пресеты топологической подсветки (ТЗ п. 2–3)."""

    WHITE_DIFFUSE = "white_diffuse"
    RGB_HIGHLIGHT = "rgb_highlight"
    OFF = "off"


HardwareTransportMode = Literal["mock", "http"]


@dataclass
class HardwareSnapshot:
    active_preset: LightingPreset
    active_brightness: int
    active_color: str
    last_capture_ack_ms: float | None
    commands_total: int
    last_error: str | None
    esp32_enabled: bool = False
    esp32_configured: bool = False
    esp32_reachable: bool | None = None
    esp32_base_url: str | None = None
    esp32_latency_ms: float | None = None
    esp32_probe_message: str | None = None
    esp32_device_info: dict[str, Any] | None = None


@dataclass
class _HardwareState:
    preset: LightingPreset = LightingPreset.OFF
    brightness: int = 80
    color: str = "#ffffff"
    last_capture_ack_ms: float | None = None
    commands: list[str] = field(default_factory=list)
    last_error: str | None = None
    esp32_reachable: bool | None = None
    esp32_latency_ms: float | None = None
    esp32_probe_message: str | None = None
    esp32_device_info: dict[str, Any] | None = None
    last_wled_state: dict[str, Any] | None = None
    debug_exchanges: list[dict[str, Any]] = field(default_factory=list)
    esp32_last_probe_monotonic: float = 0.0
    _lock: Lock = field(default_factory=Lock)

    def log(self, line: str) -> None:
        self.commands.append(f"{time.time():.3f} {line}")
        if len(self.commands) > 500:
            self.commands = self.commands[-500:]
        logger.info("hardware_gateway: %s", line)


def normalize_color(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if not v.startswith("#"):
        v = f"#{v}"
    if not _COLOR_RE.match(v):
        raise ValueError("Цвет должен быть в формате #RRGGBB")
    return v.lower()


class HardwareGateway:
    """Единая точка для команд освещения и квитанции захвата."""

    def __init__(self) -> None:
        self.transport: HardwareTransportMode = "mock"
        self._state = _HardwareState()
        self._esp32_enabled = False
        self._esp32_base_url: str | None = None
        self._esp32_health_path = "/json/info"
        self._esp32_control_path = "/json/state"
        self._esp32_segment_id = 0
        self._esp32_transition = 7
        self._esp32_color_order = "rgb"
        self._esp32_color_map = "rgb"
        self._esp32_timeout = 2.5
        self._esp32_probe_cache_sec = 3.0
        self._esp32_connection_mode = "manual"

    def reload_config(self, cfg) -> None:
        """Подхватывает настройки из ``Esp32Config`` (после load/save)."""
        from .esp32_config import Esp32Config

        if not isinstance(cfg, Esp32Config):
            raise TypeError("expected Esp32Config")
        n = cfg.normalized()
        self._esp32_enabled = n.enabled
        self._esp32_base_url = n.base_url or None
        self._esp32_health_path = n.health_path
        self._esp32_control_path = n.control_path
        self._esp32_segment_id = n.segment_id
        self._esp32_transition = n.transition
        self._esp32_color_order = n.color_order
        self._esp32_color_map = n.color_map
        self._esp32_timeout = n.timeout_sec
        self._esp32_probe_cache_sec = n.cache_sec
        self._esp32_connection_mode = n.connection_mode
        self.transport = "http" if n.is_usable() else "mock"

    @property
    def esp32_configured(self) -> bool:
        return self.transport == "http" and bool(self._esp32_base_url)

    def _record_exchange(self, exchange) -> None:
        from .esp32_http import WledHttpExchange

        if not isinstance(exchange, WledHttpExchange):
            return
        entry = {
            "ts": time.time(),
            "ok": exchange.ok,
            "method": exchange.method,
            "url": exchange.url,
            "request_body": exchange.request_body,
            "status_code": exchange.status_code,
            "response_body": exchange.response_body,
            "latency_ms": exchange.latency_ms,
            "error": exchange.error,
        }
        with self._state._lock:
            self._state.debug_exchanges.append(entry)
            if len(self._state.debug_exchanges) > 30:
                self._state.debug_exchanges = self._state.debug_exchanges[-30:]
            state_obj = exchange.response_body
            if exchange.ok and isinstance(state_obj, dict):
                if "state" in state_obj:
                    self._state.last_wled_state = state_obj.get("state")
                elif "on" in state_obj or "seg" in state_obj:
                    self._state.last_wled_state = state_obj

    def debug_exchanges(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._state._lock:
            return list(self._state.debug_exchanges[-limit:])

    def last_wled_state(self) -> dict[str, Any] | None:
        with self._state._lock:
            return self._state.last_wled_state

    def wled_debug_request(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        base_url: str | None = None,
    ):
        from .esp32_http import wled_json_request

        target = (base_url or self._esp32_base_url or "").strip()
        if not target:
            raise ValueError("Укажите адрес WLED в настройках или в запросе")
        exchange = wled_json_request(
            base_url=target,
            path=path,
            method=method,
            body=body,
            timeout_sec=self._esp32_timeout,
        )
        self._record_exchange(exchange)
        with self._state._lock:
            self._state.log(
                f"WLED_DEBUG {method} {path} ok={exchange.ok} "
                f"http={exchange.status_code}"
            )
        return exchange

    def discover_wled(
        self,
        *,
        seed_base_url: str | None = None,
        timeout_sec: float | None = None,
        use_mdns: bool = True,
        use_nodes: bool = True,
    ):
        from .wled_discovery import discover_wled_devices

        return discover_wled_devices(
            seed_base_url=seed_base_url or self._esp32_base_url,
            timeout_sec=timeout_sec or self._esp32_timeout,
            use_mdns=use_mdns,
            use_nodes=use_nodes,
        )

    def probe_esp32(self, *, force: bool = False) -> None:
        if not self.esp32_configured:
            with self._state._lock:
                self._state.esp32_reachable = None
                self._state.esp32_probe_message = (
                    "WLED не настроен — укажите адрес на вкладке «Инспекция»"
                )
            return

        now = time.monotonic()
        with self._state._lock:
            if (
                not force
                and self._state.esp32_last_probe_monotonic
                and (now - self._state.esp32_last_probe_monotonic)
                < self._esp32_probe_cache_sec
            ):
                return

        from .esp32_http import probe_esp32

        result = probe_esp32(
            base_url=self._esp32_base_url or "",
            health_path=self._esp32_health_path,
            timeout_sec=self._esp32_timeout,
        )
        if result.reachable:
            try:
                state_ex = self.wled_debug_request(
                    method="GET",
                    path="/json/state",
                )
                if not state_ex.ok:
                    pass
            except ValueError:
                pass
        with self._state._lock:
            self._state.esp32_last_probe_monotonic = now
            self._state.esp32_reachable = result.reachable
            self._state.esp32_latency_ms = result.latency_ms
            self._state.esp32_probe_message = result.message
            self._state.esp32_device_info = result.device_info
            line = (
                f"WLED_PROBE ok latency={result.latency_ms:.0f}ms"
                if result.reachable and result.latency_ms is not None
                else f"WLED_PROBE fail {result.message}"
            )
            self._state.log(line)

    def _build_control_payload(self) -> dict[str, Any]:
        with self._state._lock:
            return {
                "preset": self._state.preset.value,
                "brightness": int(self._state.brightness),
                "color": self._state.color,
            }

    def apply_lighting_control(
        self,
        *,
        preset: LightingPreset | None = None,
        brightness: int | None = None,
        color: str | None = None,
    ) -> None:
        if color is not None:
            color = normalize_color(color)
        with self._state._lock:
            if preset is not None:
                self._state.preset = preset
            if brightness is not None:
                self._state.brightness = max(0, min(100, int(brightness)))
            if color is not None:
                self._state.color = color
            self._state.log(
                f"LIGHTING {self._state.preset.value} "
                f"b={self._state.brightness} c={self._state.color}"
            )

        if self.transport == "mock":
            return

        if not self.esp32_configured:
            with self._state._lock:
                self._state.last_error = (
                    "Включите WLED и укажите адрес в настройках на вкладке «Инспекция»"
                )
            return

        self.probe_esp32(force=True)
        with self._state._lock:
            if self._state.esp32_reachable is False:
                self._state.last_error = (
                    self._state.esp32_probe_message or "WLED недоступен"
                )
                return

        from .esp32_http import send_lighting_control

        payload = self._build_control_payload()
        from .esp32_http import build_wled_state_body, wled_json_request

        wled_body = build_wled_state_body(
            preset=str(payload.get("preset") or "white_diffuse"),
            brightness_percent=int(payload.get("brightness", 80)),
            color_hex=str(payload.get("color") or "#ffffff"),
            segment_id=self._esp32_segment_id,
            transition=self._esp32_transition,
            color_order=self._esp32_color_order,
            color_map=self._esp32_color_map,
        )
        exchange = wled_json_request(
            base_url=self._esp32_base_url or "",
            path=self._esp32_control_path or "/json/state",
            method="POST",
            body=wled_body,
            timeout_sec=self._esp32_timeout,
        )
        self._record_exchange(exchange)
        ok = exchange.ok
        msg = "OK" if ok else (exchange.error or f"HTTP {exchange.status_code}")
        with self._state._lock:
            if ok:
                self._state.last_error = None
                self._state.log(f"WLED_STATE_OK {payload}")
            else:
                self._state.last_error = msg
                self._state.log(f"WLED_STATE_FAIL {msg}")

    def set_lighting_preset(self, preset: LightingPreset) -> None:
        self.apply_lighting_control(preset=preset)

    def acknowledge_capture(self) -> None:
        with self._state._lock:
            self._state.last_capture_ack_ms = time.time() * 1000.0
            self._state.log("CAPTURE_ACK")

    def snapshot(self, *, probe_esp32: bool = True) -> HardwareSnapshot:
        if probe_esp32 and self.transport == "http":
            self.probe_esp32(force=False)

        with self._state._lock:
            return HardwareSnapshot(
                active_preset=self._state.preset,
                active_brightness=self._state.brightness,
                active_color=self._state.color,
                last_capture_ack_ms=self._state.last_capture_ack_ms,
                commands_total=len(self._state.commands),
                last_error=self._state.last_error,
                esp32_enabled=self._esp32_enabled,
                esp32_configured=self.esp32_configured,
                esp32_reachable=self._state.esp32_reachable,
                esp32_base_url=self._esp32_base_url,
                esp32_latency_ms=self._state.esp32_latency_ms,
                esp32_probe_message=self._state.esp32_probe_message,
                esp32_device_info=self._state.esp32_device_info,
            )

    def recent_commands(self, limit: int = 50) -> list[str]:
        with self._state._lock:
            return list(self._state.commands[-limit:])


_gateway: HardwareGateway | None = None
_gateway_lock = Lock()


def get_hardware_gateway(db: Session) -> HardwareGateway:
    global _gateway
    from .esp32_config import esp32_config_service

    cfg = esp32_config_service.load(db)
    with _gateway_lock:
        if _gateway is None:
            _gateway = HardwareGateway()
        _gateway.reload_config(cfg)
        return _gateway


def reset_hardware_gateway_for_tests() -> None:
    global _gateway
    from .esp32_config import esp32_config_service

    with _gateway_lock:
        _gateway = None
    esp32_config_service.invalidate()
