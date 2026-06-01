"""Тесты fallback-сверки эталона без ECC."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from app.config import settings
from app.services.golden_alignment import align_rgb_with_golden_profile


def test_compare_ready_without_ecc_when_enabled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = np.full((640, 640, 3), 210, dtype=np.uint8)
    cv2.rectangle(ref, (100, 100), (200, 200), (30, 30, 30), -1)
    mov = np.full((320, 320, 3), 180, dtype=np.uint8)

    ref_path = tmp_path / "golden" / "ref.png"
    ref_path.parent.mkdir(parents=True)
    cv2.imwrite(str(ref_path), cv2.cvtColor(ref, cv2.COLOR_RGB2BGR))

    rel = "golden/ref.png"
    storage = tmp_path
    monkeypatch.setattr(settings, "storage_dir", storage)
    monkeypatch.setattr(settings, "golden_compare_without_ecc", True)

    payload = {"reference_image_rel": rel}
    with patch("app.services.golden_alignment._should_use_aligned", return_value=False):
        result = align_rgb_with_golden_profile(
            mov,
            payload_json=__import__("json").dumps(payload),
            settings=settings,
        )

    assert result.applied is False
    assert result.compare_ready is True
    assert result.rgb.shape[:2] == (640, 640)
    assert result.detail and "масштабу" in result.detail


def test_compare_skipped_without_ecc_when_disabled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = np.full((640, 640, 3), 210, dtype=np.uint8)
    mov = np.full((320, 320, 3), 180, dtype=np.uint8)
    ref_path = tmp_path / "ref.png"
    cv2.imwrite(str(ref_path), cv2.cvtColor(ref, cv2.COLOR_RGB2BGR))

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    monkeypatch.setattr(settings, "golden_compare_without_ecc", False)

    payload = {"reference_image_rel": "ref.png"}
    with patch("app.services.golden_alignment._should_use_aligned", return_value=False):
        result = align_rgb_with_golden_profile(
            mov,
            payload_json=__import__("json").dumps(payload),
            settings=settings,
        )

    assert result.applied is False
    assert result.compare_ready is False
    assert result.rgb.shape[:2] == mov.shape[:2]
