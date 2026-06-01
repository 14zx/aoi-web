"""Порядок RGB-каналов при отправке в WLED."""

from __future__ import annotations

from app.services.esp32_http import apply_color_channel_order, build_wled_state_body


def test_apply_color_channel_order_rgb():
    assert apply_color_channel_order((255, 128, 0), "rgb") == (255, 128, 0)


def test_apply_color_channel_order_swap_gb():
    assert apply_color_channel_order((255, 128, 0), "swap_gb") == (255, 0, 128)


def test_apply_color_channel_order_brg():
    assert apply_color_channel_order((255, 0, 0), "brg") == (0, 255, 0)
    assert apply_color_channel_order((0, 255, 0), "brg") == (0, 0, 255)
    assert apply_color_channel_order((0, 0, 255), "brg") == (255, 0, 0)


def test_build_wled_state_body_brg_red():
    body = build_wled_state_body(
        preset="rgb_highlight",
        brightness_percent=80,
        color_hex="#ff0000",
        color_order="brg",
    )
    col = body["seg"][0]["col"][0]
    assert col == [0, 255, 0]
