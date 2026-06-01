"""Тесты настроек админки и live-анализа."""

from __future__ import annotations

import cv2
import numpy as np

from .conftest import designate_device, login, user_id


def _frame(width=400, height=400) -> bytes:
    img = np.full((height, width, 3), 240, dtype=np.uint8)
    cv2.circle(img, (width // 2, height // 2), 30, (30, 30, 30), -1)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return buf.tobytes()


def test_settings_get_and_update(client):
    admin = login(client, "admin", "admin12345")
    res = client.get("/api/settings", headers={"Authorization": f"Bearer {admin}"})
    assert res.status_code == 200
    data = res.json()
    assert 0.0 <= data["detection_conf_threshold"] <= 1.0

    res2 = client.put(
        "/api/settings",
        headers={"Authorization": f"Bearer {admin}"},
        json={"detection_conf_threshold": 0.4, "live_analysis_interval_ms": 800},
    )
    assert res2.status_code == 200
    assert res2.json()["detection_conf_threshold"] == 0.4
    assert res2.json()["live_analysis_interval_ms"] == 800


def test_settings_operator_forbidden(client):
    op = login(client, "operator1", "operator12345")
    assert client.get("/api/settings", headers={"Authorization": f"Bearer {op}"}).status_code == 403
    assert client.put(
        "/api/settings",
        headers={"Authorization": f"Bearer {op}"},
        json={"detection_conf_threshold": 0.1},
    ).status_code == 403


def test_settings_manager_forbidden(client):
    mgr = login(client, "manager1", "manager12345")
    h = {"Authorization": f"Bearer {mgr}"}
    assert client.get("/api/settings", headers=h).status_code == 403
    assert client.put(
        "/api/settings",
        headers=h,
        json={"detection_conf_threshold": 0.33},
    ).status_code == 403


def test_live_detection_without_persistence(client):
    """Live-эндпоинт принимает кадр, возвращает JSON, не пишет в БД."""
    op = login(client, "operator1", "operator12345")
    files = {"image": ("frame.jpg", _frame(), "image/jpeg")}
    data = {"conf_threshold": "0.1"}
    res = client.post(
        "/api/inspections/live",
        headers={"Authorization": f"Bearer {op}"},
        files=files,
        data=data,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["image_width"] == 400
    assert body["image_height"] == 400
    assert "defects" in body and isinstance(body["defects"], list)
    assert body["conf_threshold"] == 0.1

    # Журнал должен остаться пустым для этого оператора.
    hist = client.get("/api/inspections", headers={"Authorization": f"Bearer {op}"}).json()
    assert hist == []


def test_inspection_with_device_and_threshold(client):
    admin = login(client, "admin", "admin12345")
    dev = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "DevA"},
    ).json()
    op = login(client, "operator1", "operator12345")
    op_id = user_id(client, admin, "operator1")
    designate_device(client, admin, dev["id"], op_id)
    client.post(f"/api/devices/{dev['id']}/take", headers={"Authorization": f"Bearer {op}"})

    files = {"image": ("test.png", _frame(800, 800), "image/png")}
    data = {"device_id": str(dev["id"]), "conf_threshold": "0.2"}
    res = client.post(
        "/api/inspections",
        headers={"Authorization": f"Bearer {op}"},
        files=files,
        data=data,
    )
    assert res.status_code == 201
    body = res.json()
    assert body["device_id"] == dev["id"]
    assert body["device_name"] == "DevA"
    assert abs(body["conf_threshold"] - 0.2) < 1e-6


def test_cannot_use_others_device(client):
    admin = login(client, "admin", "admin12345")
    dev = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "DevB"},
    ).json()
    # создаём второго
    client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {admin}"},
        json={"username": "operator2", "password": "operator12345", "role": "operator", "full_name": "Op2"},
    )
    op1 = login(client, "operator1", "operator12345")
    op2 = login(client, "operator2", "operator12345")
    op1_id = user_id(client, admin, "operator1")
    designate_device(client, admin, dev["id"], op1_id)
    client.post(f"/api/devices/{dev['id']}/take", headers={"Authorization": f"Bearer {op1}"})

    files = {"image": ("x.png", _frame(800, 800), "image/png")}
    data = {"device_id": str(dev["id"])}
    res = client.post(
        "/api/inspections",
        headers={"Authorization": f"Bearer {op2}"},
        files=files,
        data=data,
    )
    assert res.status_code == 403
