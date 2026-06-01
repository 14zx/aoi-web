"""Тесты управления устройствами (взятие/освобождение, эксклюзивность)."""

from __future__ import annotations

from .conftest import designate_device, login, user_id


def test_create_and_list_devices(client):
    admin = login(client, "admin", "admin12345")
    res = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Pixel 7", "identifier": "IMEI-1", "description": "Тест"},
    )
    assert res.status_code == 201, res.text
    dev = res.json()
    assert dev["name"] == "Pixel 7"

    op = login(client, "operator1", "operator12345")
    res = client.get("/api/devices", headers={"Authorization": f"Bearer {op}"})
    assert res.status_code == 200
    assert not any(d["name"] == "Pixel 7" for d in res.json())

    op_id = user_id(client, admin, "operator1")
    designate_device(client, admin, dev["id"], op_id)

    res = client.get("/api/devices", headers={"Authorization": f"Bearer {op}"})
    assert res.status_code == 200
    assert any(d["name"] == "Pixel 7" for d in res.json())


def test_operator_cannot_create_device(client):
    op = login(client, "operator1", "operator12345")
    res = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {op}"},
        json={"name": "Hack"},
    )
    assert res.status_code == 403


def test_take_and_release_exclusive(client):
    admin = login(client, "admin", "admin12345")
    dev = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Tablet-A"},
    ).json()

    client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {admin}"},
        json={"username": "operator2", "password": "operator12345", "role": "operator", "full_name": "Op2"},
    )

    op1 = login(client, "operator1", "operator12345")
    op2 = login(client, "operator2", "operator12345")
    op1_id = user_id(client, admin, "operator1")
    op2_id = user_id(client, admin, "operator2")

    designate_device(client, admin, dev["id"], op1_id)

    r = client.post(
        f"/api/devices/{dev['id']}/take",
        headers={"Authorization": f"Bearer {op1}"},
    )
    assert r.status_code == 200
    assert r.json()["assigned_operator_username"] == "operator1"

    r2 = client.post(
        f"/api/devices/{dev['id']}/take",
        headers={"Authorization": f"Bearer {op2}"},
    )
    assert r2.status_code == 403

    r = client.post(
        f"/api/devices/{dev['id']}/take",
        headers={"Authorization": f"Bearer {op1}"},
    )
    assert r.status_code == 200

    r3 = client.post(
        f"/api/devices/{dev['id']}/release",
        headers={"Authorization": f"Bearer {op2}"},
    )
    assert r3.status_code == 403

    r4 = client.post(
        f"/api/devices/{dev['id']}/release",
        headers={"Authorization": f"Bearer {op1}"},
    )
    assert r4.status_code == 200
    assert r4.json()["assigned_operator_id"] is None

    designate_device(client, admin, dev["id"], op2_id)
    r5 = client.post(
        f"/api/devices/{dev['id']}/take",
        headers={"Authorization": f"Bearer {op2}"},
    )
    assert r5.status_code == 200


def test_operator_cant_take_two_devices(client):
    admin = login(client, "admin", "admin12345")
    d1 = client.post("/api/devices", headers={"Authorization": f"Bearer {admin}"}, json={"name": "A"}).json()
    d2 = client.post("/api/devices", headers={"Authorization": f"Bearer {admin}"}, json={"name": "B"}).json()

    op = login(client, "operator1", "operator12345")
    op_id = user_id(client, admin, "operator1")
    designate_device(client, admin, d1["id"], op_id)
    designate_device(client, admin, d2["id"], op_id)

    assert client.post(f"/api/devices/{d1['id']}/take", headers={"Authorization": f"Bearer {op}"}).status_code == 200
    r = client.post(f"/api/devices/{d2['id']}/take", headers={"Authorization": f"Bearer {op}"})
    assert r.status_code == 409


def test_my_device_endpoint(client):
    admin = login(client, "admin", "admin12345")
    dev = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "MyPhone"},
    ).json()
    op = login(client, "operator1", "operator12345")
    assert client.get("/api/devices/mine", headers={"Authorization": f"Bearer {op}"}).json() is None

    op_id = user_id(client, admin, "operator1")
    designate_device(client, admin, dev["id"], op_id)

    client.post(f"/api/devices/{dev['id']}/take", headers={"Authorization": f"Bearer {op}"})
    mine = client.get("/api/devices/mine", headers={"Authorization": f"Bearer {op}"}).json()
    assert mine["name"] == "MyPhone"


def test_operator_sees_only_designated_devices(client):
    admin = login(client, "admin", "admin12345")
    d1 = client.post("/api/devices", headers={"Authorization": f"Bearer {admin}"}, json={"name": "Cam-A"}).json()
    d2 = client.post("/api/devices", headers={"Authorization": f"Bearer {admin}"}, json={"name": "Cam-B"}).json()

    op1_id = user_id(client, admin, "operator1")
    designate_device(client, admin, d1["id"], op1_id)

    op = login(client, "operator1", "operator12345")
    names = {d["name"] for d in client.get("/api/devices", headers={"Authorization": f"Bearer {op}"}).json()}
    assert names == {"Cam-A"}

    mgr = login(client, "manager1", "manager12345")
    designate_device(client, mgr, d2["id"], op1_id)
    names = {d["name"] for d in client.get("/api/devices", headers={"Authorization": f"Bearer {op}"}).json()}
    assert names == {"Cam-A", "Cam-B"}


def test_take_without_designation_forbidden(client):
    admin = login(client, "admin", "admin12345")
    dev = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "Unassigned"},
    ).json()
    op = login(client, "operator1", "operator12345")
    r = client.post(
        f"/api/devices/{dev['id']}/take",
        headers={"Authorization": f"Bearer {op}"},
    )
    assert r.status_code == 403
