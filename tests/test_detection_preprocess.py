"""Тесты опциональной предобработки перед детекцией."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import app.services.preprocessing as prep


def test_preprocess_disabled_returns_same_array(monkeypatch):
    monkeypatch.setattr(prep.settings, "detection_preprocess_enabled", False)
    img = np.zeros((640, 640, 3), dtype=np.uint8)
    out = prep.apply_detection_preprocess(img)
    assert out is img


def test_preprocess_bilateral_preserves_shape(monkeypatch):
    monkeypatch.setattr(prep.settings, "detection_preprocess_enabled", True)
    monkeypatch.setattr(prep.settings, "preprocess_lens_undistort", False)
    monkeypatch.setattr(prep.settings, "preprocess_noise_filter", "bilateral")
    monkeypatch.setattr(prep.settings, "preprocess_illumination", "none")
    monkeypatch.setattr(prep.settings, "detection_calibration_json", None)

    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, size=(640, 640, 3), dtype=np.uint8)
    out = prep.apply_detection_preprocess(img)
    assert out.shape == img.shape
    assert out.dtype == np.uint8


def test_detection_preprocess_has_no_gaussian_blur_call():
    """Регрессия ТЗ: в коде не должно быть вызова OpenCV GaussianBlur."""
    src = Path(prep.__file__).read_text(encoding="utf-8")
    assert "GaussianBlur(" not in src
    assert "gaussianBlur(" not in src


def test_clahe_illumination_runs(monkeypatch):
    monkeypatch.setattr(prep.settings, "detection_preprocess_enabled", True)
    monkeypatch.setattr(prep.settings, "preprocess_lens_undistort", False)
    monkeypatch.setattr(prep.settings, "preprocess_noise_filter", "none")
    monkeypatch.setattr(prep.settings, "preprocess_illumination", "clahe")
    monkeypatch.setattr(prep.settings, "preprocess_clahe_clip_limit", 2.0)
    monkeypatch.setattr(prep.settings, "preprocess_clahe_tile_grid", 8)
    monkeypatch.setattr(prep.settings, "detection_calibration_json", None)

    rng = np.random.default_rng(1)
    img = rng.integers(0, 255, size=(640, 640, 3), dtype=np.uint8)
    out = prep.apply_detection_preprocess(img)
    assert out.shape == img.shape
    assert out.dtype == np.uint8


def test_tophat_lab_runs(monkeypatch):
    monkeypatch.setattr(prep.settings, "detection_preprocess_enabled", True)
    monkeypatch.setattr(prep.settings, "preprocess_lens_undistort", False)
    monkeypatch.setattr(prep.settings, "preprocess_noise_filter", "none")
    monkeypatch.setattr(prep.settings, "preprocess_illumination", "tophat_lab")
    monkeypatch.setattr(prep.settings, "preprocess_tophat_kernel_size", 15)
    monkeypatch.setattr(prep.settings, "detection_calibration_json", None)

    img = np.full((640, 640, 3), 128, dtype=np.uint8)
    out = prep.apply_detection_preprocess(img)
    assert out.shape == (640, 640, 3)


def test_load_calibration_json_roundtrip(tmp_path):
    path = tmp_path / "cal.json"
    data = {
        "camera_matrix": [[800.0, 0.0, 320.0], [0.0, 800.0, 240.0], [0.0, 0.0, 1.0]],
        "dist_coeffs": [-0.1, 0.05, 0.0, 0.0, 0.0],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    K, d = prep.load_calibration_matrices(path)
    assert K.shape == (3, 3)
    assert d.size == 5
