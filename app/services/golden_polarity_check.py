"""Сверка полярности компонентов по маркеру на эталоне Golden Board.

После выравнивания сравнивается подзона ``polarity_marker`` на опорном
снимке и на кадре инспекции **попиксельно** (средняя |разница| по яркости).
Если область по тем же координатам сильно отличается — ``golden_polarity_wrong``.

Дополнительно для электролитов: полоска катода на противоположном крае корпуса.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from .detector import DetectedDefect, NAME_BY_CODE, _normalize_class_code
from .golden_region_check import (
    CLASS_GOLDEN_MISSING,
    CLASS_GOLDEN_WRONG,
    GoldenRegion,
    _box_iou,
    _class_matches_expected,
    _detection_hits_region,
    _expand_region,
    _normalize_expected_label,
    extract_region_tolerance_from_payload,
)

logger = logging.getLogger(__name__)

CLASS_GOLDEN_POLARITY = "golden_polarity_wrong"

POLARITY_KINDS = frozenset({"electrolytic", "diode", "ic", "generic"})

_KIND_LABEL_RU: dict[str, str] = {
    "electrolytic": "электролитический конденсатор",
    "diode": "диод",
    "ic": "микросхема",
    "generic": "компонент",
}


@dataclass(frozen=True, slots=True)
class GoldenRegionPolaritySpec:
    region: GoldenRegion
    check_polarity: bool
    polarity_kind: str
    marker: tuple[int, int, int, int] | None


def _parse_payload_dict(payload_json: str) -> dict[str, Any]:
    try:
        data: Any = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_marker(raw: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, dict):
        return None
    try:
        x1, y1, x2, y2 = int(raw["x1"]), int(raw["y1"]), int(raw["x2"]), int(raw["y2"])
    except (KeyError, TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def extract_polarity_specs_from_payload_json(payload_json: str) -> list[GoldenRegionPolaritySpec]:
    data = _parse_payload_dict(payload_json)
    raw_regions = data.get("regions")
    if not isinstance(raw_regions, list):
        return []
    specs: list[GoldenRegionPolaritySpec] = []
    for item in raw_regions:
        if not isinstance(item, dict):
            continue
        try:
            x1, y1, x2, y2 = int(item["x1"]), int(item["y1"]), int(item["x2"]), int(item["y2"])
        except (KeyError, TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        label = item.get("label")
        lab = str(label).strip() if label else None
        if lab == "":
            lab = None
        kind = str(item.get("polarity_kind") or "generic").strip().lower()
        if kind not in POLARITY_KINDS:
            kind = "generic"
        specs.append(
            GoldenRegionPolaritySpec(
                region=GoldenRegion(x1=x1, y1=y1, x2=x2, y2=y2, label=lab),
                check_polarity=bool(item.get("check_polarity")),
                polarity_kind=kind,
                marker=_parse_marker(item.get("polarity_marker")),
            )
        )
    return specs


_ELECTROLYTIC_CLASS_HINTS = frozenset(
    {
        "scapacitor",
        "smd_electrolytic_cap",
        "electrolytic_cap",
        "cap_electrolytic",
        "electrolytic",
    }
)


def _infer_polarity_kind(polarity_kind: str, label: str | None) -> str:
    """Подставляет electrolytic/diode по классу зоны, если в разметке стоит generic."""
    kind = polarity_kind if polarity_kind in POLARITY_KINDS else "generic"
    if kind != "generic":
        return kind
    norm = _normalize_expected_label(label) or ""
    if norm in _ELECTROLYTIC_CLASS_HINTS:
        return "electrolytic"
    if any(h in norm for h in ("electrolyt", "scap", "e_cap")):
        return "electrolytic"
    if "capacitor" in norm and "ceramic" not in norm:
        return "electrolytic"
    if norm in ("diode", "smd_diode") or norm.endswith("_diode"):
        return "diode"
    return kind


def _mean_luma(rgb: np.ndarray, box: tuple[int, int, int, int]) -> float:
    crop = _crop_rgb(rgb, box)
    if crop is None:
        return 128.0
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return float(np.mean(gray))


def _electrolytic_stripe_mismatch(
    reference_rgb: np.ndarray,
    inspection_rgb: np.ndarray,
    marker: tuple[int, int, int, int],
    region: GoldenRegion,
) -> tuple[bool, float, float]:
    """Сравнивает тёмную полоску катода на маркере и на противоположном краю корпуса."""
    min_delta = float(getattr(settings, "golden_polarity_stripe_min_delta", 8.0))
    ref_m = _mean_luma(reference_rgb, marker)
    opps = _opposite_markers_in_region(marker, region)
    if not opps:
        return False, ref_m, ref_m
    ref_o = _mean_luma(reference_rgb, opps[0])
    test_m = _mean_luma(inspection_rgb, marker)
    test_o = _mean_luma(inspection_rgb, opps[0])

    ref_stripe = ref_o - ref_m
    if ref_stripe < min_delta:
        return False, ref_m, test_m

    test_stripe_at_marker = test_o - test_m
    test_stripe_on_opposite = test_m - test_o

    # Полоска на эталоне у маркера, на инспекции — на противоположном крае.
    wrong = test_stripe_on_opposite >= min_delta * 0.55 and test_stripe_at_marker < min_delta * 0.45
    score = test_stripe_on_opposite if wrong else test_stripe_at_marker
    return wrong, ref_m, score


def _default_marker_for_region(
    region: GoldenRegion,
    *,
    polarity_kind: str,
) -> tuple[int, int, int, int]:
    """Эвристика, если маркер не нарисован вручную."""
    w = region.x2 - region.x1
    h = region.y2 - region.y1
    if polarity_kind == "ic":
        mw = max(4, int(w * 0.28))
        mh = max(4, int(h * 0.28))
        return region.x1, region.y1, region.x1 + mw, region.y1 + mh
    if polarity_kind == "electrolytic" and h >= w * 0.75:
        # SMD-«бочонок» сверху: полоска катода обычно у нижнего края зоны.
        mh = max(4, int(h * 0.2))
        return region.x1, region.y2 - mh, region.x2, region.y2
    mw = max(4, int(w * 0.22))
    return region.x1, region.y1, region.x1 + mw, region.y2


def _pixel_mae(ref_crop: np.ndarray | None, test_crop: np.ndarray | None) -> float:
    """Средняя абсолютная разница яркости (0..255), меньше — ближе к эталону."""
    if ref_crop is None or test_crop is None:
        return 999.0
    if ref_crop.shape != test_crop.shape:
        test_crop = cv2.resize(
            test_crop,
            (ref_crop.shape[1], ref_crop.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    ref_g = cv2.cvtColor(ref_crop, cv2.COLOR_RGB2GRAY).astype(np.float32)
    test_g = cv2.cvtColor(test_crop, cv2.COLOR_RGB2GRAY).astype(np.float32)
    return float(np.mean(np.abs(ref_g - test_g)))


def _enrich_marker_for_ncc(
    marker: tuple[int, int, int, int],
    region: GoldenRegion,
) -> tuple[int, int, int, int]:
    """Расширяет узкий маркер (только линия катода), чтобы NCC видел контраст корпуса."""
    x1, y1, x2, y2 = marker
    rw = region.x2 - region.x1
    rh = region.y2 - region.y1
    if rw < 4 or rh < 4:
        return marker

    min_w = max(8, int(rw * 0.45))
    min_h = max(8, int(rh * 0.35))

    rcx = (region.x1 + region.x2) / 2.0
    rcy = (region.y1 + region.y2) / 2.0
    mcx = (x1 + x2) / 2.0
    mcy = (y1 + y2) / 2.0

    if (x2 - x1) < min_w:
        cx = int(mcx)
        nx1 = max(region.x1, cx - min_w // 2)
        nx2 = min(region.x2, nx1 + min_w)
        nx1 = max(region.x1, nx2 - min_w)
        x1, x2 = nx1, nx2

    if (y2 - y1) < min_h:
        if mcy >= rcy:
            y1 = max(region.y1, y2 - min_h)
        elif mcy <= rcy:
            y2 = min(region.y2, y1 + min_h)
        else:
            cy = int(mcy)
            y1 = max(region.y1, cy - min_h // 2)
            y2 = min(region.y2, y1 + min_h)

    # Полоска у нижнего/верхнего края — подтянуть к центру корпуса.
    if mcy >= rcy and (y2 - y1) < int(rh * 0.42):
        y1 = max(region.y1, y2 - max(min_h, int(rh * 0.38)))
    elif mcy <= rcy and (y2 - y1) < int(rh * 0.42):
        y2 = min(region.y2, y1 + max(min_h, int(rh * 0.38)))

    if mcx <= rcx and (x2 - x1) < int(rw * 0.42):
        x2 = min(region.x2, x1 + max(min_w, int(rw * 0.38)))
    elif mcx >= rcx and (x2 - x1) < int(rw * 0.42):
        x1 = max(region.x1, x2 - max(min_w, int(rw * 0.38)))

    return x1, y1, x2, y2


def _resolve_marker(
    spec: GoldenRegionPolaritySpec,
    *,
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int] | None:
    marker = spec.marker
    if marker is None and spec.check_polarity:
        kind = _infer_polarity_kind(spec.polarity_kind, spec.region.label)
        marker = _default_marker_for_region(spec.region, polarity_kind=kind)
    if marker is None:
        return None
    marker = _enrich_marker_for_ncc(marker, spec.region)
    x1, y1, x2, y2 = marker
    if x2 > frame_width or y2 > frame_height or x1 < 0 or y1 < 0:
        return None
    if x2 - x1 < 3 or y2 - y1 < 3:
        return None
    return x1, y1, x2, y2


def _crop_rgb(rgb: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray | None:
    x1, y1, x2, y2 = box
    h, w = rgb.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 + 1 or y2 <= y1 + 1:
        return None
    return rgb[y1:y2, x1:x2].copy()


def _opposite_markers_in_region(
    marker: tuple[int, int, int, int],
    region: GoldenRegion,
) -> list[tuple[int, int, int, int]]:
    """Зеркальные позиции маркера на противоположном краю корпуса."""
    x1, y1, x2, y2 = marker
    mw = max(3, x2 - x1)
    mh = max(3, y2 - y1)
    rcx = (region.x1 + region.x2) / 2.0
    rcy = (region.y1 + region.y2) / 2.0
    mcx = (x1 + x2) / 2.0
    mcy = (y1 + y2) / 2.0
    alts: list[tuple[int, int, int, int]] = []

    if mcy >= rcy:
        ny1, ny2 = region.y1, region.y1 + mh
    else:
        ny1, ny2 = region.y2 - mh, region.y2
    alts.append((x1, ny1, x2, ny2))

    if mcx <= rcx:
        nx1, nx2 = region.x2 - mw, region.x2
    else:
        nx1, nx2 = region.x1, region.x1 + mw
    alts.append((nx1, y1, nx2, y2))

    uniq: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for box in alts:
        bx1 = max(region.x1, min(box[0], box[2]))
        by1 = max(region.y1, min(box[1], box[3]))
        bx2 = min(region.x2, max(box[0], box[2]))
        by2 = min(region.y2, max(box[1], box[3]))
        if bx2 - bx1 < 3 or by2 - by1 < 3:
            continue
        key = (bx1, by1, bx2, by2)
        if key == (x1, y1, x2, y2) or key in seen:
            continue
        seen.add(key)
        uniq.append(key)
    return uniq


def marker_polarity_mismatch(
    reference_rgb: np.ndarray,
    inspection_rgb: np.ndarray,
    marker: tuple[int, int, int, int],
    *,
    polarity_kind: str = "generic",
    region: GoldenRegion | None = None,
) -> tuple[bool, float, float]:
    """``True``, если зона маркера на инспекции сильно отличается от эталона."""
    if region is not None:
        marker = _enrich_marker_for_ncc(marker, region)

    ref_crop = _crop_rgb(reference_rgb, marker)
    test_crop = _crop_rgb(inspection_rgb, marker)
    if ref_crop is None or test_crop is None:
        return False, 0.0, 0.0

    effective_kind = _infer_polarity_kind(polarity_kind, region.label if region else None)
    if effective_kind == "electrolytic" and region is not None:
        stripe_wrong, _, stripe_score = _electrolytic_stripe_mismatch(
            reference_rgb, inspection_rgb, marker, region
        )
        if stripe_wrong:
            return True, stripe_score, stripe_score

    mae = _pixel_mae(ref_crop, test_crop)
    max_mae = float(getattr(settings, "golden_polarity_max_pixel_mae", 24.0))

    if mae <= max_mae:
        return False, mae, mae

    best_alt = mae
    if region is not None:
        for opp_box in _opposite_markers_in_region(marker, region):
            opp_crop = _crop_rgb(inspection_rgb, opp_box)
            best_alt = min(best_alt, _pixel_mae(ref_crop, opp_crop))

    # Полоска/pin1 «переехали» на противоположный край — типичная неверная полярность.
    if best_alt < mae * 0.72 and best_alt <= max_mae * 1.15:
        return True, mae, best_alt

    return True, mae, best_alt


def _class_display(code: str) -> str:
    return NAME_BY_CODE.get(code, code)


def _kind_label(kind: str) -> str:
    return _KIND_LABEL_RU.get(kind, _KIND_LABEL_RU["generic"])


def _make_polarity_defect(
    region: GoldenRegion,
    *,
    polarity_kind: str,
    expected_label: str | None,
) -> DetectedDefect:
    comp = _class_display(expected_label) if expected_label else _kind_label(polarity_kind)
    name = f"Неверная полярность ({comp})"
    return DetectedDefect(
        class_code=CLASS_GOLDEN_POLARITY,
        class_name=name,
        confidence=0.87,
        x1=region.x1,
        y1=region.y1,
        x2=region.x2,
        y2=region.y2,
    )


def _region_blocked_by_etalon_defect(
    defects: list[DetectedDefect],
    region: GoldenRegion,
    *,
    min_iou: float,
) -> bool:
    blocked = frozenset(
        {
            CLASS_GOLDEN_MISSING,
            CLASS_GOLDEN_WRONG,
            CLASS_GOLDEN_POLARITY,
            "component_missing",
            "component_wrong_package",
        }
    )
    for d in defects:
        if d.class_code not in blocked:
            continue
        if _box_iou(d.x1, d.y1, d.x2, d.y2, region.x1, region.y1, region.x2, region.y2) >= min_iou:
            return True
    return False


def _component_present_in_region(
    defects: list[DetectedDefect],
    region: GoldenRegion,
    expected: str | None,
    *,
    min_iou: float,
    tolerance_px: int,
    frame_width: int,
    frame_height: int,
) -> bool:
    check_region = _expand_region(
        region,
        tolerance_px=tolerance_px,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    hits = [d for d in defects if _detection_hits_region(d, check_region, min_iou=min_iou)]
    if not expected:
        return bool(hits)
    return any(_class_matches_expected(d.class_code, expected) for d in hits)


def apply_golden_polarity_checks(
    defects: list[DetectedDefect],
    *,
    inspection_rgb: np.ndarray,
    reference_rgb: np.ndarray | None,
    payload_json: str,
    db: Session,  # noqa: ARG001 — зарезервировано для будущих правил семантики
    frame_width: int,
    frame_height: int,
    golden_compare_ready: bool,
) -> list[DetectedDefect]:
    if not getattr(settings, "golden_polarity_check_enabled", True):
        return defects
    if not golden_compare_ready or reference_rgb is None:
        return defects

    specs = [s for s in extract_polarity_specs_from_payload_json(payload_json) if s.check_polarity]
    if not specs:
        return defects

    tol = extract_region_tolerance_from_payload(payload_json)
    min_iou = float(getattr(settings, "golden_region_min_iou", 0.2))
    extra: list[DetectedDefect] = []

    for spec in specs:
        region = spec.region
        if region.x2 > frame_width or region.y2 > frame_height:
            continue
        if _region_blocked_by_etalon_defect(defects + extra, region, min_iou=min_iou):
            continue

        expected = _normalize_expected_label(region.label)
        if not _component_present_in_region(
            defects,
            region,
            expected,
            min_iou=min_iou,
            tolerance_px=tol,
            frame_width=frame_width,
            frame_height=frame_height,
        ):
            logger.info(
                "Polarity skip: component not in region label=%s region=%s",
                expected,
                region,
            )
            continue

        marker = _resolve_marker(spec, frame_width=frame_width, frame_height=frame_height)
        if marker is None:
            logger.warning(
                "Polarity marker invalid for region (%d,%d)-(%d,%d)",
                region.x1,
                region.y1,
                region.x2,
                region.y2,
            )
            continue

        effective_kind = _infer_polarity_kind(spec.polarity_kind, region.label)
        wrong, orig, flip_score = marker_polarity_mismatch(
            reference_rgb,
            inspection_rgb,
            marker,
            polarity_kind=effective_kind,
            region=region,
        )
        if not wrong:
            logger.debug(
                "Polarity OK kind=%s region=%s mae=%.1f alt_mae=%.1f",
                spec.polarity_kind,
                region,
                orig,
                flip_score,
            )
            continue

        extra.append(
            _make_polarity_defect(
                region,
                polarity_kind=effective_kind,
                expected_label=expected or region.label,
            )
        )
        logger.info(
            "Golden polarity mismatch kind=%s label=%s mae=%.1f alt_mae=%.1f marker=%s",
            effective_kind,
            expected,
            orig,
            flip_score,
            marker,
        )

    if not extra:
        return defects
    return list(defects) + extra


def load_reference_rgb_from_payload(payload_json: str, storage_dir) -> np.ndarray | None:
    """Загружает опорный снимок эталона из ``payload_json``."""
    from .golden_alignment import extract_reference_image_rel, resolve_reference_path
    from .preprocessing import ImageValidationError, apply_detection_preprocess, load_image

    data = _parse_payload_dict(payload_json)
    rel = extract_reference_image_rel(data)
    if not rel:
        return None
    try:
        path = resolve_reference_path(storage_dir, rel)
        rgb = load_image(path)
        return apply_detection_preprocess(rgb)
    except (ValueError, ImageValidationError, OSError) as exc:
        logger.warning("Не удалось загрузить эталон для полярности: %s", exc)
        return None
