"""Тесты устройств-камер и трансляции кадров (админское добавление + phone).

Новый flow:
1. Администратор ``POST /api/devices`` → система возвращает upload_token
   и streaming_link ``/phone?device=<id>&token=<token>``.
2. Телефон публикует кадры ``POST /api/devices/{id}/frame`` с заголовком
   ``X-Device-Token`` (без JWT).
3. Оператор с ПК смотрит кадры через ``GET /api/devices/{id}/frame.jpg``
   (JWT) или MJPEG-стрим ``/stream?token=<JWT>``.
"""

from __future__ import annotations

import cv2
import numpy as np

from .conftest import designate_device, login, user_id


def _jpeg(width=320, height=240) -> bytes:
    img = np.full((height, width, 3), 128, dtype=np.uint8)
    cv2.rectangle(img, (30, 30), (width - 30, height - 30), (10, 200, 10), 5)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return buf.tobytes()


def _admin_create(client, name: str, token: str) -> dict:
    res = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_admin_create_device_returns_token_and_link(client):
    admin = login(client, "admin", "admin12345")
    body = _admin_create(client, "Phone-Admin1", admin)
    assert body["name"] == "Phone-Admin1"
    assert body["registered_by_username"] == "admin"
    assert isinstance(body["upload_token"], str) and len(body["upload_token"]) > 20
    # Ссылка указывает на /phone с device_id и токеном.
    link = body["streaming_link"]
    assert link and "/phone?" in link
    assert f"device={body['id']}" in link
    assert f"token={body['upload_token']}" in link


def test_operator_cannot_create_device(client):
    op = login(client, "operator1", "operator12345")
    res = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {op}"},
        json={"name": "IllegalPhone"},
    )
    assert res.status_code == 403


def test_publish_and_fetch_frame(client):
    admin = login(client, "admin", "admin12345")
    reg = _admin_create(client, "Phone-A", admin)
    dev_id = reg["id"]
    token = reg["upload_token"]

    # Публикация кадра телефоном — БЕЗ JWT, с X-Device-Token.
    frame = _jpeg()
    res = client.post(
        f"/api/devices/{dev_id}/frame",
        headers={"X-Device-Token": token},
        files={"image": ("frame.jpg", frame, "image/jpeg")},
    )
    assert res.status_code == 204

    # Оператор видит, что устройство «в эфире» (после закрепления).
    op = login(client, "operator1", "operator12345")
    op_id = user_id(client, admin, "operator1")
    designate_device(client, admin, dev_id, op_id)
    lst = client.get("/api/devices", headers={"Authorization": f"Bearer {op}"}).json()
    mine = next(d for d in lst if d["id"] == dev_id)
    assert mine["is_streaming"] is True
    assert mine["last_seen_at"] is not None
    # Скрытый ли токен у оператора (он не видит его).
    assert mine.get("upload_token") is None
    assert mine["has_upload_token"] is True

    got = client.get(
        f"/api/devices/{dev_id}/frame.jpg",
        headers={"Authorization": f"Bearer {op}"},
    )
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("image/")
    assert got.content == frame


def test_publish_requires_valid_token(client):
    admin = login(client, "admin", "admin12345")
    reg = _admin_create(client, "Phone-B", admin)
    dev_id = reg["id"]

    bad = client.post(
        f"/api/devices/{dev_id}/frame",
        headers={"X-Device-Token": "wrong-token"},
        files={"image": ("f.jpg", _jpeg(), "image/jpeg")},
    )
    assert bad.status_code == 401

    missing = client.post(
        f"/api/devices/{dev_id}/frame",
        files={"image": ("f.jpg", _jpeg(), "image/jpeg")},
    )
    assert missing.status_code == 401


def test_fetch_frame_requires_auth(client):
    admin = login(client, "admin", "admin12345")
    reg = _admin_create(client, "Phone-C", admin)
    client.post(
        f"/api/devices/{reg['id']}/frame",
        headers={"X-Device-Token": reg["upload_token"]},
        files={"image": ("f.jpg", _jpeg(), "image/jpeg")},
    )
    anon = client.get(f"/api/devices/{reg['id']}/frame.jpg")
    assert anon.status_code == 401


def test_frame_not_available_yet(client):
    admin = login(client, "admin", "admin12345")
    reg = _admin_create(client, "Phone-D", admin)

    op = login(client, "operator1", "operator12345")
    res = client.get(
        f"/api/devices/{reg['id']}/frame.jpg",
        headers={"Authorization": f"Bearer {op}"},
    )
    assert res.status_code == 404


def test_regenerate_token_invalidates_old(client):
    admin = login(client, "admin", "admin12345")
    reg = _admin_create(client, "Phone-E", admin)
    dev_id = reg["id"]
    old_token = reg["upload_token"]

    # Кадр со старым токеном работает.
    ok = client.post(
        f"/api/devices/{dev_id}/frame",
        headers={"X-Device-Token": old_token},
        files={"image": ("f.jpg", _jpeg(), "image/jpeg")},
    )
    assert ok.status_code == 204

    # Регенерируем токен.
    res = client.post(
        f"/api/devices/{dev_id}/regenerate-token",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert res.status_code == 200
    new_token = res.json()["upload_token"]
    assert new_token and new_token != old_token
    assert res.json()["is_streaming"] is False  # last_seen сброшен

    # Старый токен отвергается.
    bad = client.post(
        f"/api/devices/{dev_id}/frame",
        headers={"X-Device-Token": old_token},
        files={"image": ("f.jpg", _jpeg(), "image/jpeg")},
    )
    assert bad.status_code == 401

    # Новый — работает.
    good = client.post(
        f"/api/devices/{dev_id}/frame",
        headers={"X-Device-Token": new_token},
        files={"image": ("f.jpg", _jpeg(), "image/jpeg")},
    )
    assert good.status_code == 204


def test_public_info_requires_upload_token(client):
    admin = login(client, "admin", "admin12345")
    reg = _admin_create(client, "Phone-F", admin)
    dev_id = reg["id"]

    # Корректный токен — 200.
    ok = client.get(f"/api/devices/public/{dev_id}?token={reg['upload_token']}")
    assert ok.status_code == 200
    assert ok.json()["name"] == "Phone-F"

    # Некорректный — 401.
    bad = client.get(f"/api/devices/public/{dev_id}?token=nope")
    assert bad.status_code == 401


def test_get_link_returns_streaming_url(client):
    admin = login(client, "admin", "admin12345")
    reg = _admin_create(client, "Phone-G", admin)
    dev_id = reg["id"]

    res = client.get(
        f"/api/devices/{dev_id}/link",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["streaming_link"]
    assert body["upload_token"] == reg["upload_token"]

    # Обычный оператор — не имеет доступа.
    op = login(client, "operator1", "operator12345")
    forbidden = client.get(
        f"/api/devices/{dev_id}/link",
        headers={"Authorization": f"Bearer {op}"},
    )
    assert forbidden.status_code == 403


def test_operator_can_take_admin_created_device(client):
    admin = login(client, "admin", "admin12345")
    client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {admin}"},
        json={"username": "operator2", "password": "operator12345", "role": "operator", "full_name": "Op2"},
    )
    reg = _admin_create(client, "SharedPhone", admin)
    dev_id = reg["id"]

    op2 = login(client, "operator2", "operator12345")
    op2_id = user_id(client, admin, "operator2")
    designate_device(client, admin, dev_id, op2_id)
    r = client.post(
        f"/api/devices/{dev_id}/take",
        headers={"Authorization": f"Bearer {op2}"},
    )
    assert r.status_code == 200
    assert r.json()["assigned_operator_username"] == "operator2"
