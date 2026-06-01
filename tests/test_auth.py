"""Тесты аутентификации и ролевой модели (ТЗ 4.8.1, 4.8.3)."""

from __future__ import annotations

from .conftest import login


def test_login_success(client):
    token = login(client, "admin", "admin12345")
    assert isinstance(token, str) and len(token) > 10

    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["username"] == "admin"
    assert res.json()["role"] == "admin"


def test_login_bad_password(client):
    res = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "wrong"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 401


def test_lockout_after_5_failed_attempts(client):
    """ТЗ 4.8.1: неверный ввод >5 раз подряд блокирует аккаунт на 15 минут."""
    for _ in range(5):
        client.post(
            "/api/auth/login",
            data={"username": "operator1", "password": "bad"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    # Шестая попытка — даже с верным паролем — должна вернуть 423 Locked
    res = client.post(
        "/api/auth/login",
        data={"username": "operator1", "password": "operator12345"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 423


def test_operator_cannot_access_users(client):
    token = login(client, "operator1", "operator12345")
    res = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_manager_can_list_users(client):
    token = login(client, "manager1", "manager12345")
    res = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert any(u["username"] == "admin" for u in res.json())


def test_manager_cannot_create_user(client):
    token = login(client, "manager1", "manager12345")
    res = client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "username": "x_new_user",
            "password": "password12345",
            "role": "operator",
            "full_name": "X",
        },
    )
    assert res.status_code == 403


def test_manager_can_block_operator(client):
    token = login(client, "manager1", "manager12345")
    h = {"Authorization": f"Bearer {token}"}
    users = client.get("/api/users", headers=h).json()
    op_id = next(u["id"] for u in users if u["username"] == "operator1")
    res = client.patch(f"/api/users/{op_id}", headers=h, json={"is_active": False})
    assert res.status_code == 200
    assert res.json()["is_active"] is False
    res2 = client.post(f"/api/users/{op_id}/unlock", headers=h)
    assert res2.status_code == 200
    assert res2.json()["is_active"] is True


def test_manager_cannot_block_admin(client):
    token = login(client, "manager1", "manager12345")
    h = {"Authorization": f"Bearer {token}"}
    users = client.get("/api/users", headers=h).json()
    admin_id = next(u["id"] for u in users if u["username"] == "admin")
    res = client.patch(f"/api/users/{admin_id}", headers=h, json={"is_active": False})
    assert res.status_code == 403
    assert "администратора" in res.json()["detail"].lower()
    res2 = client.post(f"/api/users/{admin_id}/unlock", headers=h)
    assert res2.status_code == 403


def test_admin_can_set_user_password(client):
    admin = login(client, "admin", "admin12345")
    h = {"Authorization": f"Bearer {admin}"}
    users = client.get("/api/users", headers=h).json()
    op_id = next(u["id"] for u in users if u["username"] == "operator1")
    res = client.post(
        f"/api/users/{op_id}/password",
        headers=h,
        json={"new_password": "newpass12345"},
    )
    assert res.status_code == 200
    assert login(client, "operator1", "newpass12345")
    # вернуть пароль для других тестов
    client.post(
        f"/api/users/{op_id}/password",
        headers=h,
        json={"new_password": "operator12345"},
    )


def test_manager_cannot_set_user_password(client):
    token = login(client, "manager1", "manager12345")
    h = {"Authorization": f"Bearer {token}"}
    users = client.get("/api/users", headers=h).json()
    op_id = next(u["id"] for u in users if u["username"] == "operator1")
    res = client.post(
        f"/api/users/{op_id}/password",
        headers=h,
        json={"new_password": "hackpass123"},
    )
    assert res.status_code == 403


def test_manager_cannot_delete_device(client):
    admin = login(client, "admin", "admin12345")
    dev = client.post(
        "/api/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={"name": "DelTestCam"},
    ).json()
    mgr = login(client, "manager1", "manager12345")
    res = client.delete(
        f"/api/devices/{dev['id']}",
        headers={"Authorization": f"Bearer {mgr}"},
    )
    assert res.status_code == 403


def test_manager_cannot_access_settings(client):
    token = login(client, "manager1", "manager12345")
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/settings", headers=h).status_code == 403
    assert client.put(
        "/api/settings",
        headers=h,
        json={"detection_conf_threshold": 0.3},
    ).status_code == 403
