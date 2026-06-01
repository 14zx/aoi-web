"""Тесты удалённого управления телефоном: очередь команд и обмен статусом.

Сценарий: админ создаёт устройство (получает ``upload_token``), оператор
«берёт его в работу», отправляет команды (старт/стоп записи, подсветка,
качество), телефон опрашивает их по своему токену и публикует статус.
"""

from __future__ import annotations

from .conftest import designate_device, login, user_id


def _create_and_take(client) -> tuple[dict, str, str]:
    admin = login(client, "admin", "admin12345")
    dev = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Ctrl-Phone"},
    ).json()
    assert dev.get("upload_token"), "токен должен быть выдан при создании"
    op = login(client, "operator1", "operator12345")
    op_id = user_id(client, admin, "operator1")
    designate_device(client, admin, dev["id"], op_id)
    r = client.post(
        f"/api/devices/{dev['id']}/take",
        headers={"Authorization": f"Bearer {op}"},
    )
    assert r.status_code == 200, r.text
    return dev, op, admin


def test_operator_enqueues_commands_and_phone_drains_them(client):
    dev, op, _ = _create_and_take(client)
    did = dev["id"]
    token = dev["upload_token"]
    auth = {"Authorization": f"Bearer {op}"}

    # Пустая очередь в начале.
    r = client.get(
        f"/api/devices/{did}/commands",
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 200
    assert r.json()["commands"] == []

    # Оператор отправляет набор команд.
    for cmd in [
        {"command": "start"},
        {"command": "torch_on"},
        {"command": "quality", "value": "fhd"},
        {"command": "flip"},
        {"command": "stop"},
    ]:
        r = client.post(f"/api/devices/{did}/control", headers=auth, json=cmd)
        assert r.status_code == 202, r.text

    # Телефон получает их одним запросом (и очищает очередь).
    r = client.get(
        f"/api/devices/{did}/commands",
        headers={"X-Device-Token": token},
    )
    assert r.status_code == 200
    names = [c["command"] for c in r.json()["commands"]]
    assert names == ["start", "torch_on", "quality", "flip", "stop"]

    # После drain очередь снова пуста.
    r = client.get(
        f"/api/devices/{did}/commands",
        headers={"X-Device-Token": token},
    )
    assert r.json()["commands"] == []


def test_command_validation(client):
    dev, op, _ = _create_and_take(client)
    did = dev["id"]
    auth = {"Authorization": f"Bearer {op}"}

    r = client.post(f"/api/devices/{did}/control", headers=auth, json={"command": "explode"})
    assert r.status_code == 400

    r = client.post(
        f"/api/devices/{did}/control",
        headers=auth,
        json={"command": "quality", "value": "cosmic"},
    )
    assert r.status_code == 400

    r = client.post(
        f"/api/devices/{did}/control",
        headers=auth,
        json={"command": "quality"},
    )
    assert r.status_code == 400


def test_foreign_operator_cannot_control(client):
    dev, _op, admin = _create_and_take(client)
    did = dev["id"]
    # Заводим второго оператора, он не берёт устройство.
    client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {admin}"},
        json={"username": "operator2", "password": "operator12345", "role": "operator", "full_name": "Op2"},
    )
    op2 = login(client, "operator2", "operator12345")
    r = client.post(
        f"/api/devices/{did}/control",
        headers={"Authorization": f"Bearer {op2}"},
        json={"command": "stop"},
    )
    assert r.status_code == 403

    # Руководитель может управлять в любом случае.
    r = client.post(
        f"/api/devices/{did}/control",
        headers={"Authorization": f"Bearer {admin}"},
        json={"command": "stop"},
    )
    assert r.status_code == 202


def test_phone_reports_status_and_pc_reads_it(client):
    dev, op, _ = _create_and_take(client)
    did = dev["id"]
    token = dev["upload_token"]

    # Телефон сообщает состояние.
    r = client.post(
        f"/api/devices/{did}/status",
        headers={"X-Device-Token": token},
        json={"is_streaming": True, "preset": "hd", "torch_on": True, "facing": "environment"},
    )
    assert r.status_code == 204

    # PC читает.
    r = client.get(
        f"/api/devices/{did}/status",
        headers={"Authorization": f"Bearer {op}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_streaming"] is True
    assert data["preset"] == "hd"
    assert data["torch_on"] is True
    assert data["facing"] == "environment"


def test_phone_requires_valid_token(client):
    dev, _, _ = _create_and_take(client)
    did = dev["id"]
    for path in (f"/api/devices/{did}/commands", f"/api/devices/{did}/status"):
        r = client.get(path)
        assert r.status_code in (401, 405)  # commands=GET 401, status=GET ok w/o token? GET status requires JWT

    # Wrong token for drain.
    r = client.get(
        f"/api/devices/{did}/commands",
        headers={"X-Device-Token": "nope"},
    )
    assert r.status_code == 401

    # Wrong token for status publish.
    r = client.post(
        f"/api/devices/{did}/status",
        headers={"X-Device-Token": "nope"},
        json={"is_streaming": False},
    )
    assert r.status_code == 401


def test_device_link_uses_public_base_url_from_env(client, monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://192.168.99.10:8000")
    dev, _, _admin = _create_and_take(client)
    mgr = login(client, "manager1", "manager12345")
    r = client.get(
        f"/api/devices/{dev['id']}/link",
        headers={"Authorization": f"Bearer {mgr}"},
    )
    assert r.status_code == 200, r.text
    link = r.json().get("streaming_link") or ""
    assert link.startswith("https://192.168.99.10:8000/phone?device=")

    meta = client.get("/api/meta").json()
    assert meta.get("public_base_url") == "https://192.168.99.10:8000"
