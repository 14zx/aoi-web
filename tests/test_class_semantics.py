"""Тесты семантики классов (компонент / дефект)."""

from __future__ import annotations

from app.services.class_semantics import (
    counts_as_protocol_defect,
    lookup_semantic_entry,
    semantic_kind_for_class,
)


def test_lookup_case_insensitive():
    m = {"IC": {"kind": "component", "label": "", "review_required": False}}
    assert lookup_semantic_entry("ic", m)["kind"] == "component"
    assert lookup_semantic_entry("IC", m)["kind"] == "component"


def test_counts_component_not_protocol_defect():
    m = {"smd_capacitor": {"kind": "component", "label": "", "review_required": False}}
    assert semantic_kind_for_class("smd_capacitor", m) == "component"
    assert counts_as_protocol_defect("smd_capacitor", m) is False


def test_placement_tilt_counts_as_defect():
    assert counts_as_protocol_defect("placement_tilt", {}) is True
    assert counts_as_protocol_defect("golden_polarity_wrong", {}) is True


def test_registry_component_without_semantics_mapping():
    """Классы с is_defect=false в реестре — компоненты, даже без записи в class_semantics."""
    from app.services.detector import DEFECT_CLASSES

    comp = next((c for c in DEFECT_CLASSES if c.get("is_defect") is False), None)
    if comp is None:
        return
    assert semantic_kind_for_class(comp["code"], {}) == "component"
    assert counts_as_protocol_defect(comp["code"], {}) is False
