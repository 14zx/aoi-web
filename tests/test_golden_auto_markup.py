"""Тесты авторазметки Golden Board."""

from __future__ import annotations

from app.services.detector import DetectedDefect
from app.services.golden_auto_markup import detections_to_golden_regions


def test_detections_to_regions_filters_pcb_defects() -> None:
    defects = [
        DetectedDefect("open", "open", 0.9, 10, 10, 50, 50),
        DetectedDefect("component_missing", "missing", 0.8, 100, 100, 140, 140),
    ]
    regions = detections_to_golden_regions(defects, mappings={})
    assert len(regions) == 1
    assert regions[0]["label"] == "component_missing"
    assert regions[0]["x1"] == 100
