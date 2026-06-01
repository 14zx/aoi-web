"""Тесты журнала аудита (API для администратора)."""

from __future__ import annotations

from .conftest import login


def test_operator_cannot_view_audit(client):
    token = login(client, "operator1", "operator12345")
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/audit", headers=h).status_code == 403
    assert client.get("/api/audit/export.csv", headers=h).status_code == 403


def test_admin_lists_and_exports_audit(client):
    admin = login(client, "admin", "admin12345")
    h = {"Authorization": f"Bearer {admin}"}
    r = client.get("/api/audit", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data and "total" in data
    assert data["total"] >= 1
    assert any(row["action"] == "login_success" for row in data["items"])

    r2 = client.get("/api/audit/export.csv", headers=h)
    assert r2.status_code == 200
    assert "text/csv" in r2.headers.get("content-type", "")
    body = r2.content.decode("utf-8-sig")
    assert "Дата и время" in body
    assert "login_success" in body


def test_audit_filter_by_user(client):
    admin = login(client, "admin", "admin12345")
    h = {"Authorization": f"Bearer {admin}"}
    users = client.get("/api/users", headers=h).json()
    op = next(u for u in users if u["username"] == "operator1")
    client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {login(client, 'operator1', 'operator12345')}"},
    )
    login(client, "operator1", "operator12345")
    r = client.get(f"/api/audit?user_id={op['id']}", headers=h)
    assert r.status_code == 200
    for row in r.json()["items"]:
        assert row["user_id"] == op["id"] or row["username"] == "operator1"


def test_audit_filter_by_date(client):
    admin = login(client, "admin", "admin12345")
    h = {"Authorization": f"Bearer {admin}"}
    today = client.get("/api/audit", headers=h).json()["items"][0]["created_at"][:10]
    r = client.get(f"/api/audit?from_date={today}&to_date={today}", headers=h)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_manager_sees_only_operator_audit(client):
    mgr = login(client, "manager1", "manager12345")
    admin = login(client, "admin", "admin12345")
    h_mgr = {"Authorization": f"Bearer {mgr}"}
    h_admin = {"Authorization": f"Bearer {admin}"}

    r = client.get("/api/audit", headers=h_mgr)
    assert r.status_code == 200
    users = client.get("/api/users", headers=h_admin).json()
    op_ids = {u["id"] for u in users if u["role"] == "operator"}
    admin_ids = {u["id"] for u in users if u["role"] == "admin"}
    for row in r.json()["items"]:
        assert row["user_id"] in op_ids

    admin_id = next(iter(admin_ids))
    assert client.get(f"/api/audit?user_id={admin_id}", headers=h_mgr).status_code == 403

    op_id = next(iter(op_ids))
    r2 = client.get(f"/api/audit?user_id={op_id}", headers=h_mgr)
    assert r2.status_code == 200
    for row in r2.json()["items"]:
        assert row["user_id"] == op_id
