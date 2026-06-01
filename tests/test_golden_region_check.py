"""Тесты сверки детекций с разметкой Golden Board."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

import app.services.golden_region_check as grc
from app.services.detector import DetectedDefect


@pytest.fixture
def db(client) -> Session:  # noqa: ARG001 — поднимает схему БД
    from app.database import SessionLocal

    with SessionLocal() as session:
        yield session


def _defect(code: str, x1: int, y1: int, x2: int, y2: int, conf: float = 0.9) -> DetectedDefect:
    return DetectedDefect(
        class_code=code,
        class_name=code,
        confidence=conf,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def test_extract_regions_from_payload() -> None:
    payload = {
        "regions": [
            {"x1": 10, "y1": 10, "x2": 50, "y2": 50, "label": "component_missing"},
            {"x1": 0, "y1": 0, "x2": 0, "y2": 5},
        ]
    }
    regs = grc.extract_regions_from_payload(payload)
    assert len(regs) == 1
    assert regs[0].label == "component_missing"


def test_missing_when_expected_class_not_found(db: Session) -> None:
    payload = json.dumps(
        {"regions": [{"x1": 100, "y1": 100, "x2": 200, "y2": 200, "label": "open"}]}
    )
    defects = [_defect("short", 10, 10, 30, 30)]
    out = grc.apply_golden_region_checks(
        defects,
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=True,
    )
    assert len(out) == 2
    added = [d for d in out if d.class_code == grc.CLASS_GOLDEN_MISSING]
    assert len(added) == 1
    assert "Не найден" in added[0].class_name
    assert added[0].x1 == 100


def test_no_extra_when_detection_matches_label(db: Session) -> None:
    payload = json.dumps(
        {"regions": [{"x1": 100, "y1": 100, "x2": 200, "y2": 200, "label": "open"}]}
    )
    defects = [_defect("open", 110, 110, 190, 190)]
    out = grc.apply_golden_region_checks(
        defects,
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=True,
    )
    assert len(out) == 1


def test_wrong_when_class_differs(db: Session) -> None:
    payload = json.dumps(
        {"regions": [{"x1": 50, "y1": 50, "x2": 150, "y2": 150, "label": "open"}]}
    )
    defects = [_defect("short", 60, 60, 140, 140)]
    out = grc.apply_golden_region_checks(
        defects,
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=True,
    )
    wrong = [d for d in out if d.class_code == grc.CLASS_GOLDEN_WRONG]
    assert len(wrong) == 1
    assert "Не тот компонент" in wrong[0].class_name


def test_missing_when_no_label_and_no_hits(db: Session) -> None:
    payload = json.dumps({"regions": [{"x1": 20, "y1": 20, "x2": 80, "y2": 80}]})
    out = grc.apply_golden_region_checks(
        [],
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=True,
    )
    assert len(out) == 1
    assert out[0].class_code == grc.CLASS_GOLDEN_MISSING


def test_tolerance_from_profile_payload(db: Session) -> None:
    payload = json.dumps(
        {
            "region_tolerance_px": 24,
            "regions": [{"x1": 100, "y1": 100, "x2": 150, "y2": 150, "label": "open"}],
        }
    )
    defects = [_defect("open", 155, 105, 175, 145)]
    out = grc.apply_golden_region_checks(
        defects,
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=True,
    )
    assert len(out) == 1


def test_extract_region_tolerance_from_payload() -> None:
    assert grc.extract_region_tolerance_from_payload(json.dumps({"region_tolerance_px": 20})) == 20
    assert grc.extract_region_tolerance_from_payload(json.dumps({"regions": []})) == 12


def test_tolerance_allows_nearby_detection(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grc.settings, "golden_region_tolerance_px", 20)
    payload = json.dumps(
        {"regions": [{"x1": 100, "y1": 100, "x2": 150, "y2": 150, "label": "open"}]}
    )
    # bbox чуть смещён, но центр попадает в расширенную зону
    defects = [_defect("open", 155, 105, 175, 145)]
    out = grc.apply_golden_region_checks(
        defects,
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=True,
        tolerance_px=24,
    )
    assert len(out) == 1


def test_skipped_without_alignment(db: Session) -> None:
    payload = json.dumps({"regions": [{"x1": 20, "y1": 20, "x2": 80, "y2": 80}]})
    out = grc.apply_golden_region_checks(
        [],
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=False,
    )
    assert out == []


def test_disabled_via_settings(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grc.settings, "golden_region_check_enabled", False)
    payload = json.dumps({"regions": [{"x1": 20, "y1": 20, "x2": 80, "y2": 80}]})
    out = grc.apply_golden_region_checks(
        [],
        payload_json=payload,
        db=db,
        frame_width=640,
        frame_height=640,
        golden_compare_ready=True,
    )
    assert out == []
