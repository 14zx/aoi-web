"""Тесты проверки полярности по маркеру Golden Board."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.services.detector import DetectedDefect
from app.services.golden_polarity_check import (
    CLASS_GOLDEN_POLARITY,
    apply_golden_polarity_checks,
    marker_polarity_mismatch,
)


@pytest.fixture
def db(client) -> Session:  # noqa: ARG001
    from app.database import SessionLocal

    with SessionLocal() as session:
        yield session


def _defect(code: str, x1: int, y1: int, x2: int, y2: int) -> DetectedDefect:
    return DetectedDefect(
        class_code=code,
        class_name=code,
        confidence=0.9,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def _cap_with_band(*, width: int = 80, height: int = 40, band_left: bool = True) -> np.ndarray:
    """Синтетический корпус с тёмной полосой слева или справа."""
    rgb = np.full((height, width, 3), 210, dtype=np.uint8)
    if band_left:
        rgb[:, : max(8, width // 5)] = 35
    else:
        rgb[:, width - max(8, width // 5) :] = 35
    return rgb


def test_marker_polarity_detects_horizontal_flip() -> None:
    ref = _cap_with_band(band_left=True)
    ok = _cap_with_band(band_left=True)
    bad = _cap_with_band(band_left=False)
    marker = (0, 0, 80, 40)
    wrong_ok, _, _ = marker_polarity_mismatch(ref, ok, marker, polarity_kind="electrolytic")
    wrong_bad, _, _ = marker_polarity_mismatch(ref, bad, marker, polarity_kind="electrolytic")
    assert wrong_ok is False
    assert wrong_bad is True


def _diode_vertical(*, band_bottom: bool = True, width: int = 30, height: int = 60) -> np.ndarray:
    """Вертикальный SMD-диод: полоска катода снизу или сверху."""
    rgb = np.full((height, width, 3), 210, dtype=np.uint8)
    band_h = max(6, height // 6)
    if band_bottom:
        rgb[height - band_h :, :] = 35
    else:
        rgb[:band_h, :] = 35
    return rgb


def test_marker_polarity_detects_vertical_flip_for_diode() -> None:
    """Как на плате: диод стоит вертикально, катодная полоска снизу vs сверху."""
    from app.services.golden_region_check import GoldenRegion

    ref = _diode_vertical(band_bottom=True)
    ok = ref.copy()
    bad = _diode_vertical(band_bottom=False)
    region = GoldenRegion(x1=0, y1=0, x2=30, y2=60, label="diode")
    # Узкий маркер только на линии (как в редакторе эталона).
    marker = (0, 52, 30, 60)
    wrong_ok, _, _ = marker_polarity_mismatch(
        ref, ok, marker, polarity_kind="diode", region=region
    )
    wrong_bad, _, _ = marker_polarity_mismatch(
        ref, bad, marker, polarity_kind="diode", region=region
    )
    assert wrong_ok is False
    assert wrong_bad is True


def test_apply_polarity_after_matching_component(db: Session) -> None:
    def cap(*, band_left: bool = True) -> np.ndarray:
        rgb = np.full((40, 80, 3), 210, dtype=np.uint8)
        if band_left:
            rgb[:, :16] = 35
        else:
            rgb[:, 64:] = 35
        return rgb

    def frame(*, band_left: bool, ox: int = 40, oy: int = 50) -> np.ndarray:
        rgb = np.full((200, 200, 3), 220, dtype=np.uint8)
        rgb[oy : oy + 40, ox : ox + 80] = cap(band_left=band_left)
        return rgb

    ref = frame(band_left=True)
    test_ok = ref.copy()
    test_bad = frame(band_left=False)

    payload = json.dumps(
        {
            "regions": [
                {
                    "x1": 40,
                    "y1": 50,
                    "x2": 120,
                    "y2": 90,
                    "label": "smd_electrolytic_cap",
                    "check_polarity": True,
                    "polarity_kind": "electrolytic",
                    "polarity_marker": {"x1": 40, "y1": 50, "x2": 120, "y2": 90},
                }
            ]
        }
    )
    defects = [_defect("smd_electrolytic_cap", 42, 52, 118, 88)]

    out_ok = apply_golden_polarity_checks(
        defects,
        inspection_rgb=test_ok,
        reference_rgb=ref,
        payload_json=payload,
        db=db,
        frame_width=200,
        frame_height=200,
        golden_compare_ready=True,
    )
    assert len(out_ok) == 1

    out_bad = apply_golden_polarity_checks(
        list(defects),
        inspection_rgb=test_bad,
        reference_rgb=ref,
        payload_json=payload,
        db=db,
        frame_width=200,
        frame_height=200,
        golden_compare_ready=True,
    )
    pol = [d for d in out_bad if d.class_code == CLASS_GOLDEN_POLARITY]
    assert len(pol) == 1
    assert "полярность" in pol[0].class_name.lower()


def test_electrolytic_stripe_detects_band_on_opposite_edge() -> None:
    """SMD-бочонок сверху: полоска катода снизу vs сверху."""
    from app.services.golden_region_check import GoldenRegion
    from app.services.golden_polarity_check import _electrolytic_stripe_mismatch

    def cap(*, band_bottom: bool = True, size: int = 200) -> np.ndarray:
        rgb = np.full((size, size, 3), 200, dtype=np.uint8)
        band_h = max(10, size // 10)
        if band_bottom:
            rgb[size - band_h :, :] = 40
        else:
            rgb[:band_h, :] = 40
        return rgb

    region = GoldenRegion(x1=0, y1=0, x2=200, y2=200, label="scapacitor")
    marker = (0, 170, 200, 200)
    ref = cap(band_bottom=True)
    ok = cap(band_bottom=True)
    bad = cap(band_bottom=False)

    w_ok, _, _ = _electrolytic_stripe_mismatch(ref, ok, marker, region)
    w_bad, _, _ = _electrolytic_stripe_mismatch(ref, bad, marker, region)
    assert w_ok is False
    assert w_bad is True


def test_infer_polarity_kind_for_scapacitor() -> None:
    from app.services.golden_polarity_check import _infer_polarity_kind

    assert _infer_polarity_kind("generic", "scapacitor") == "electrolytic"
    assert _infer_polarity_kind("generic", "smd_electrolytic_cap") == "electrolytic"
    assert _infer_polarity_kind("diode", "scapacitor") == "diode"


def test_polarity_skipped_without_compare_ready(db: Session) -> None:
    payload = json.dumps(
        {
            "regions": [
                {
                    "x1": 10,
                    "y1": 10,
                    "x2": 50,
                    "y2": 50,
                    "check_polarity": True,
                    "polarity_marker": {"x1": 12, "y1": 12, "x2": 20, "y2": 48},
                }
            ]
        }
    )
    ref = _cap_with_band()
    out = apply_golden_polarity_checks(
        [],
        inspection_rgb=ref,
        reference_rgb=ref,
        payload_json=payload,
        db=db,
        frame_width=80,
        frame_height=40,
        golden_compare_ready=False,
    )
    assert out == []


def test_polarity_skipped_when_component_missing(db: Session) -> None:
    payload = json.dumps(
        {
            "regions": [
                {
                    "x1": 10,
                    "y1": 10,
                    "x2": 50,
                    "y2": 50,
                    "label": "open",
                    "check_polarity": True,
                    "polarity_marker": {"x1": 12, "y1": 12, "x2": 20, "y2": 48},
                }
            ]
        }
    )
    ref = _cap_with_band()
    flipped = cv2.flip(ref, 1)
    out = apply_golden_polarity_checks(
        [_defect("short", 5, 5, 8, 8)],
        inspection_rgb=flipped,
        reference_rgb=ref,
        payload_json=payload,
        db=db,
        frame_width=80,
        frame_height=40,
        golden_compare_ready=True,
    )
    assert not any(d.class_code == CLASS_GOLDEN_POLARITY for d in out)
