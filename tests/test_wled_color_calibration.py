"""Калибровка и карта каналов WLED."""

from __future__ import annotations

import pytest

from app.services.esp32_color_map import (
    apply_color_map,
    color_map_from_calibration,
    resolve_color_map,
)
from app.services.esp32_http import apply_color_channel_order


def test_color_map_from_calibration_cycle_brg_symptom():
    """Без фикса: R→синий, G→красный, B→зелёный на ленте."""
    obs = {"r": "b", "g": "r", "b": "g"}
    assert color_map_from_calibration(obs) == "brg"


def test_apply_color_map_brg_fixes_red():
    assert apply_color_map((255, 0, 0), "brg") == (0, 255, 0)


def test_resolve_custom_map():
    assert resolve_color_map("custom", "brg") == "brg"
    assert resolve_color_map("brg", None) == "brg"


def test_calibration_duplicate_rejected():
    with pytest.raises(ValueError, match="разных"):
        color_map_from_calibration({"r": "b", "g": "b", "b": "g"})


def test_apply_color_channel_order_custom():
    assert apply_color_channel_order((255, 0, 0), "custom", color_map="brg") == (0, 255, 0)
