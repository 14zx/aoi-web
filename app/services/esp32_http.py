"""HTTP-клиент WLED: probe через ``/json/info``, управление через ``/json/state``.

См. https://kno.wled.ge/interfaces/json-api/
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Esp32ProbeResult:
    reachable: bool
    latency_ms: float | None
    message: str
    device_info: dict[str, Any] | None = None


@dataclass(frozen=True)
class WledHttpExchange:
    ok: bool
    method: str
    url: str
    request_body: Any
    status_code: int | None
    response_body: Any
    latency_ms: float | None
    error: str | None = None


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _parse_hex_rgb(color: str) -> tuple[int, int, int]:
    h = color.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError("Цвет должен быть в формате #RRGGBB")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def apply_color_channel_order(
    rgb: tuple[int, int, int],
    color_order: str = "rgb",
    *,
    color_map: str | None = None,
) -> tuple[int, int, int]:
    """Порядок каналов для ``seg.col`` (логический RGB из #RRGGBB).

    Пресеты: ``rgb``, ``swap_gb`` (G↔B), ``brg`` (R→B, G→R, B→G на ленте).
    ``custom`` — тройка ``color_map`` (например ``brg``), см. ``esp32_color_map``.
    """
    from .esp32_color_map import apply_color_map, resolve_color_map

    m = resolve_color_map(color_order, color_map)
    return apply_color_map(rgb, m)


def _percent_to_bri(percent: int) -> int:
    """0–100 % → 1–255; 0 % означает «выкл.» (см. доку: не использовать bri=0 при on)."""
    p = int(percent)
    if p <= 0:
        return 0
    return max(1, min(255, round(p * 255 / 100)))


def build_wled_state_body(
    *,
    preset: str,
    brightness_percent: int,
    color_hex: str,
    segment_id: int = 0,
    transition: int = 7,
    color_order: str = "rgb",
    color_map: str | None = None,
) -> dict[str, Any]:
    """Тело POST ``/json/state`` для WLED (с ``v: true`` — вернуть полный state)."""
    body: dict[str, Any] = {"v": True, "transition": max(0, min(65535, int(transition)))}

    if preset == "off":
        body["on"] = False
        return body

    bri = _percent_to_bri(brightness_percent)
    if bri == 0:
        body["on"] = False
        return body

    seg_id = max(0, int(segment_id))
    body["on"] = True
    body["bri"] = bri
    body["mainseg"] = seg_id

    if preset == "white_diffuse":
        rgb = (255, 255, 255)
    else:
        rgb = _parse_hex_rgb(color_hex)
    rgb = apply_color_channel_order(rgb, color_order, color_map=color_map)

    body["seg"] = [
        {
            "id": seg_id,
            "sel": True,
            "fx": 0,
            "col": [[rgb[0], rgb[1], rgb[2]], [0, 0, 0], [0, 0, 0]],
        }
    ]
    return body


def wled_json_request(
    *,
    base_url: str,
    path: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout_sec: float = 2.5,
) -> WledHttpExchange:
    """Произвольный запрос к WLED JSON API (отладка администратора)."""
    url = _join_url(base_url, path)
    m = method.upper()
    data: bytes | None = None
    if body is not None and m in ("POST", "PUT", "PATCH"):
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=m,
        headers={"Content-Type": "application/json"} if data else {},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            raw = resp.read().decode("utf-8", errors="replace")
            parsed: Any = raw
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = raw
            return WledHttpExchange(
                ok=resp.status in (200, 204),
                method=m,
                url=url,
                request_body=body,
                status_code=resp.status,
                response_body=parsed,
                latency_ms=latency_ms,
            )
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        detail = exc.read().decode("utf-8", errors="replace")
        parsed_err: Any = detail
        if detail.strip():
            try:
                parsed_err = json.loads(detail)
            except json.JSONDecodeError:
                parsed_err = detail[:2000]
        return WledHttpExchange(
            ok=False,
            method=m,
            url=url,
            request_body=body,
            status_code=exc.code,
            response_body=parsed_err,
            latency_ms=latency_ms,
            error=f"HTTP {exc.code}",
        )
    except urllib.error.URLError as exc:
        return WledHttpExchange(
            ok=False,
            method=m,
            url=url,
            request_body=body,
            status_code=None,
            response_body=None,
            latency_ms=None,
            error=f"Нет связи: {exc.reason}",
        )
    except TimeoutError:
        return WledHttpExchange(
            ok=False,
            method=m,
            url=url,
            request_body=body,
            status_code=None,
            response_body=None,
            latency_ms=None,
            error="Таймаут",
        )
    except OSError as exc:
        return WledHttpExchange(
            ok=False,
            method=m,
            url=url,
            request_body=body,
            status_code=None,
            response_body=None,
            latency_ms=None,
            error=str(exc),
        )


def probe_esp32(
    *,
    base_url: str,
    health_path: str,
    timeout_sec: float,
) -> Esp32ProbeResult:
    """GET ``/json/info`` (или legacy path) — контроллер WLED в сети."""
    url = _join_url(base_url, health_path or "/json/info")
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                return Esp32ProbeResult(False, latency_ms, f"HTTP {resp.status}", None)
            info: dict[str, Any] | None = None
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        info = parsed.get("info") if "info" in parsed else parsed
                    else:
                        info = {"value": parsed}
                except json.JSONDecodeError:
                    info = {"raw": raw[:500]}
            name = ""
            if isinstance(info, dict):
                name = str(info.get("name") or "WLED")
                leds = info.get("leds") if isinstance(info.get("leds"), dict) else {}
                count = leds.get("count") if isinstance(leds, dict) else None
                msg = f"WLED {name}" + (f", {count} LED" if count else "")
            else:
                msg = "WLED подключён"
            return Esp32ProbeResult(True, latency_ms, msg, info)
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return Esp32ProbeResult(False, latency_ms, f"HTTP {exc.code}", None)
    except urllib.error.URLError as exc:
        return Esp32ProbeResult(False, None, f"Нет связи: {exc.reason}", None)
    except TimeoutError:
        return Esp32ProbeResult(False, None, "Таймаут ожидания ответа WLED", None)
    except OSError as exc:
        return Esp32ProbeResult(False, None, str(exc), None)


def send_lighting_control(
    *,
    base_url: str,
    control_path: str,
    payload: dict[str, Any],
    timeout_sec: float,
    segment_id: int = 0,
    transition: int = 7,
    color_order: str = "rgb",
    color_map: str | None = None,
) -> tuple[bool, str]:
    """POST JSON на WLED ``/json/state``."""
    preset = str(payload.get("preset") or "white_diffuse")
    brightness = int(payload.get("brightness", 80))
    color = str(payload.get("color") or "#ffffff")
    body = build_wled_state_body(
        preset=preset,
        brightness_percent=brightness,
        color_hex=color,
        segment_id=segment_id,
        transition=transition,
        color_order=color_order,
        color_map=color_map,
    )
    exchange = wled_json_request(
        base_url=base_url,
        path=control_path or "/json/state",
        method="POST",
        body=body,
        timeout_sec=timeout_sec,
    )
    if exchange.ok:
        return True, "OK"
    return False, exchange.error or f"HTTP {exchange.status_code}"


def send_lighting_preset(
    *,
    base_url: str,
    preset_path: str,
    preset: str,
    timeout_sec: float,
    brightness: int | None = None,
    color: str | None = None,
    segment_id: int = 0,
    transition: int = 7,
    color_order: str = "rgb",
    color_map: str | None = None,
) -> tuple[bool, str]:
    payload: dict[str, Any] = {
        "preset": preset,
        "brightness": brightness if brightness is not None else 80,
    }
    if color is not None:
        payload["color"] = color
    return send_lighting_control(
        base_url=base_url,
        control_path=preset_path or "/json/state",
        payload=payload,
        timeout_sec=timeout_sec,
        segment_id=segment_id,
        transition=transition,
        color_order=color_order,
        color_map=color_map,
    )
