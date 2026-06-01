"""HTTP: этапы пайплайна АОИ — освещение, захват, демо выравнивания (ТЗ п. 3)."""

from __future__ import annotations

import io
import logging

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..schemas.pipeline import (
    AlignmentDemoOut,
    CaptureAckOut,
    Esp32HardwareConfigIn,
    Esp32HardwareConfigOut,
    HardwareStatusOut,
    LightingControlIn,
    LightingControlOut,
    LightingPresetIn,
    LightingPresetOut,
    WledAdminDiagnosticsOut,
    WledDebugRequestIn,
    WledDiscoverIn,
    WledDiscoverOut,
    WledDiscoveredDeviceOut,
    WledHttpExchangeOut,
)
from ..services.alignment import align_rgb_ecc
from ..services.esp32_config import Esp32Config, esp32_config_service
from ..services.hardware_gateway import LightingPreset, get_hardware_gateway
from ..services.preprocessing import ImageValidationError
from .deps import require_admin, require_any

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline", tags=["Пайплайн АОИ"])


def _preset_from_str(value: str) -> LightingPreset:
    try:
        return LightingPreset(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестный пресет: {value}",
        ) from exc


def _lighting_control_out(gw) -> LightingControlOut:
    snap = gw.snapshot(probe_esp32=False)
    return LightingControlOut(
        preset=snap.active_preset.value,
        brightness=snap.active_brightness,
        color=snap.active_color,
        transport=gw.transport,
    )


def _hardware_status_out(gw, *, probe: bool = True) -> HardwareStatusOut:
    snap = gw.snapshot(probe_esp32=probe)
    return HardwareStatusOut(
        active_preset=snap.active_preset.value,
        active_brightness=snap.active_brightness,
        active_color=snap.active_color,
        last_capture_ack_ms=snap.last_capture_ack_ms,
        commands_total=snap.commands_total,
        last_error=snap.last_error,
        transport=gw.transport,
        esp32_enabled=snap.esp32_enabled,
        esp32_configured=snap.esp32_configured,
        esp32_reachable=snap.esp32_reachable,
        esp32_latency_ms=snap.esp32_latency_ms,
        esp32_probe_message=snap.esp32_probe_message,
    )


def _exchange_out(exchange) -> WledHttpExchangeOut:
    return WledHttpExchangeOut(
        ok=exchange.ok,
        method=exchange.method,
        url=exchange.url,
        request_body=exchange.request_body,
        status_code=exchange.status_code,
        response_body=exchange.response_body,
        latency_ms=exchange.latency_ms,
        error=exchange.error,
    )


def _admin_diagnostics_out(gw, db: Session) -> WledAdminDiagnosticsOut:
    from ..services.esp32_config import esp32_config_service

    cfg = esp32_config_service.load(db).normalized()
    snap = gw.snapshot(probe_esp32=True)
    exchanges = [
        _exchange_out_row(row)
        for row in gw.debug_exchanges(20)
    ]
    return WledAdminDiagnosticsOut(
        esp32_enabled=snap.esp32_enabled,
        esp32_configured=snap.esp32_configured,
        esp32_base_url=snap.esp32_base_url,
        connection_mode=cfg.connection_mode,
        esp32_reachable=snap.esp32_reachable,
        esp32_latency_ms=snap.esp32_latency_ms,
        esp32_probe_message=snap.esp32_probe_message,
        esp32_device_info=snap.esp32_device_info,
        last_wled_state=gw.last_wled_state(),
        last_error=snap.last_error,
        recent_commands=gw.recent_commands(40),
        debug_exchanges=exchanges,
    )


def _exchange_out_row(row: dict) -> WledHttpExchangeOut:
    return WledHttpExchangeOut(
        ok=bool(row.get("ok")),
        method=str(row.get("method") or ""),
        url=str(row.get("url") or ""),
        request_body=row.get("request_body"),
        status_code=row.get("status_code"),
        response_body=row.get("response_body"),
        latency_ms=row.get("latency_ms"),
        error=row.get("error"),
    )


def _config_out(cfg: Esp32Config) -> Esp32HardwareConfigOut:
    n = cfg.normalized()
    return Esp32HardwareConfigOut(
        enabled=n.enabled,
        base_url=n.base_url,
        connection_mode=n.connection_mode,  # type: ignore[arg-type]
        health_path=n.health_path,
        control_path=n.control_path,
        segment_id=n.segment_id,
        transition=n.transition,
        color_order=n.color_order,  # type: ignore[arg-type]
        color_map=n.color_map,
        color_cal=n.color_cal,
        timeout_sec=n.timeout_sec,
        cache_sec=n.cache_sec,
    )


@router.get("/hardware/config", response_model=Esp32HardwareConfigOut)
def get_esp32_hardware_config(
    _: object = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Esp32HardwareConfigOut:
    return _config_out(esp32_config_service.load(db))


@router.put("/hardware/config", response_model=Esp32HardwareConfigOut)
def update_esp32_hardware_config(
    body: Esp32HardwareConfigIn,
    _: object = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Esp32HardwareConfigOut:
    current = esp32_config_service.load(db)
    data = current.normalized()
    updates = body.model_dump(exclude_none=True)
    merged = Esp32Config(
        enabled=updates.get("enabled", data.enabled),
        base_url=updates.get("base_url", data.base_url),
        connection_mode=updates.get("connection_mode", data.connection_mode),
        health_path=updates.get("health_path", data.health_path),
        control_path=updates.get("control_path", data.control_path),
        segment_id=updates.get("segment_id", data.segment_id),
        transition=updates.get("transition", data.transition),
        color_order=updates.get("color_order", data.color_order),
        color_map=updates.get("color_map", data.color_map),
        color_cal=updates.get("color_cal", data.color_cal),
        timeout_sec=updates.get("timeout_sec", data.timeout_sec),
        cache_sec=updates.get("cache_sec", data.cache_sec),
    )
    if merged.enabled and merged.connection_mode == "manual" and not merged.base_url.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Укажите адрес WLED (например http://192.168.50.130) или включите авто-поиск",
        )
    saved = esp32_config_service.save(db, merged)
    get_hardware_gateway(db).reload_config(saved)
    return _config_out(saved)


@router.post("/lighting/control", response_model=LightingControlOut)
def set_lighting_control(
    body: LightingControlIn,
    _: object = Depends(require_any),
    db: Session = Depends(get_db),
) -> LightingControlOut:
    if body.preset is None and body.brightness is None and body.color is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Укажите preset, brightness и/или color",
        )
    gw = get_hardware_gateway(db)
    preset = _preset_from_str(body.preset) if body.preset else None
    gw.apply_lighting_control(
        preset=preset,
        brightness=body.brightness,
        color=body.color,
    )
    return _lighting_control_out(gw)


@router.post("/lighting/preset", response_model=LightingPresetOut)
def set_lighting_preset(
    body: LightingPresetIn,
    _: object = Depends(require_any),
    db: Session = Depends(get_db),
) -> LightingPresetOut:
    gw = get_hardware_gateway(db)
    preset = _preset_from_str(body.preset)
    gw.set_lighting_preset(preset)
    return LightingPresetOut(**_lighting_control_out(gw).model_dump())


@router.post("/capture/ack", response_model=CaptureAckOut)
def acknowledge_capture(
    _: object = Depends(require_any),
    db: Session = Depends(get_db),
) -> CaptureAckOut:
    get_hardware_gateway(db).acknowledge_capture()
    return CaptureAckOut()


@router.get("/hardware/status", response_model=HardwareStatusOut)
def hardware_status(
    _: object = Depends(require_any),
    db: Session = Depends(get_db),
) -> HardwareStatusOut:
    gw = get_hardware_gateway(db)
    return _hardware_status_out(gw, probe=True)


@router.post("/hardware/probe", response_model=HardwareStatusOut)
def hardware_probe(
    _: object = Depends(require_any),
    db: Session = Depends(get_db),
) -> HardwareStatusOut:
    gw = get_hardware_gateway(db)
    gw.probe_esp32(force=True)
    return _hardware_status_out(gw, probe=False)


@router.post("/hardware/discover", response_model=WledDiscoverOut)
def hardware_discover_wled(
    body: WledDiscoverIn,
    _: object = Depends(require_admin),
    db: Session = Depends(get_db),
) -> WledDiscoverOut:
    gw = get_hardware_gateway(db)
    result = gw.discover_wled(
        seed_base_url=body.seed_base_url,
        timeout_sec=body.timeout_sec,
        use_mdns=body.use_mdns,
        use_nodes=body.use_nodes,
    )
    return WledDiscoverOut(
        devices=[WledDiscoveredDeviceOut(**d) for d in result.devices],
        methods_used=result.methods_used,
        errors=result.errors,
        duration_ms=result.duration_ms,
    )


@router.get("/hardware/admin/diagnostics", response_model=WledAdminDiagnosticsOut)
def hardware_admin_diagnostics(
    _: object = Depends(require_admin),
    db: Session = Depends(get_db),
) -> WledAdminDiagnosticsOut:
    gw = get_hardware_gateway(db)
    return _admin_diagnostics_out(gw, db)


@router.post("/hardware/admin/debug-request", response_model=WledHttpExchangeOut)
def hardware_admin_debug_request(
    body: WledDebugRequestIn,
    _: object = Depends(require_admin),
    db: Session = Depends(get_db),
) -> WledHttpExchangeOut:
    gw = get_hardware_gateway(db)
    try:
        exchange = gw.wled_debug_request(
            method=body.method,
            path=body.path,
            body=body.body,
            base_url=body.base_url,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _exchange_out(exchange)


def _pil_to_rgb_u8(data: bytes) -> np.ndarray:
    """RGB uint8 H×W×3 (без проверки MIN_RESOLUTION из load_image)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.asarray(img, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ImageValidationError("Ожидается RGB-изображение.")
    return np.ascontiguousarray(arr)


@router.post("/alignment/demo", response_model=AlignmentDemoOut)
async def alignment_demo(
    reference: UploadFile = File(..., description="Эталон (RGB)"),
    moving: UploadFile = File(..., description="Сдвинутый кадр (RGB)"),
    _: object = Depends(require_any),
) -> AlignmentDemoOut:
    """Сравнивает MAE до/после ECC между двумя загруженными кадрами (комиссионный тест)."""
    try:
        ref_bytes = await reference.read()
        mov_bytes = await moving.read()
        ref_rgb = _pil_to_rgb_u8(ref_bytes)
        mov_rgb = _pil_to_rgb_u8(mov_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("alignment_demo decode: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось прочитать изображения (JPEG/PNG).",
        ) from exc

    if ref_rgb.shape[0] < 64 or ref_rgb.shape[1] < 64:
        raise HTTPException(status_code=400, detail="Эталон слишком маленький (минимум 64×64).")

    mov_resized = mov_rgb
    if mov_resized.shape[:2] != ref_rgb.shape[:2]:
        import cv2

        mov_resized = cv2.resize(
            mov_rgb,
            (ref_rgb.shape[1], ref_rgb.shape[0]),
            interpolation=cv2.INTER_AREA,
        )

    ref_g = np.mean(ref_rgb, axis=2)
    mov_g = np.mean(mov_resized, axis=2)
    mae_before = float(np.mean(np.abs(mov_g.astype(np.float32) - ref_g.astype(np.float32))))

    _, mae_after_raw = align_rgb_ecc(
        mov_rgb,
        ref_rgb,
        max_iters=settings.alignment_ecc_max_iters,
        motion=settings.alignment_ecc_motion,
    )
    mae_after = mae_after_raw if mae_after_raw != float("inf") else mae_before

    ok = mae_after < mae_before * 0.98 or mae_after < 2.0
    return AlignmentDemoOut(
        ok=bool(ok),
        mae_before=mae_before,
        mae_after=float(mae_after),
        message=None
        if ok
        else "ECC не улучшил выравнивание; проверьте снимки или включите более контрастный эталон.",
    )


__all__ = ["router"]
