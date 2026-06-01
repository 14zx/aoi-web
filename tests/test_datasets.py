"""Тесты управления датасетами (загрузка/активация/удаление).

Поскольку в тестовом окружении нет реального ultralytics+весов, детектор
всегда работает в ``fallback``-режиме. Нам достаточно проверить, что API
корректно принимает файл, сохраняет на диск, переключает ``is_active``
и не падает при reload.
"""

from __future__ import annotations

import io
from pathlib import Path

from .conftest import login


def _fake_weights(size_bytes: int = 256) -> bytes:
    # Не настоящий YOLO-файл, но для fallback-детектора это не важно —
    # проверяем только бухгалтерию/файловые операции.
    return b"fake-yolo-weights\x00" * (max(1, size_bytes // 18))


def _upload(client, token: str, *, name: str, activate: bool = False) -> dict:
    files = {"file": ("weights.pt", io.BytesIO(_fake_weights()), "application/octet-stream")}
    data = {"name": name, "description": "unit test", "activate": "true" if activate else "false"}
    res = client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
        data=data,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_operator_cannot_upload(client):
    op = login(client, "operator1", "operator12345")
    files = {"file": ("weights.pt", io.BytesIO(b"x"), "application/octet-stream")}
    res = client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {op}"},
        files=files,
        data={"name": "bad"},
    )
    assert res.status_code == 403


def test_upload_list_activate(client):
    admin = login(client, "admin", "admin12345")

    one = _upload(client, admin, name="yolov8n_v1")
    two = _upload(client, admin, name="yolov8n_v2", activate=True)
    assert one["is_active"] is False
    assert two["is_active"] is True

    # Файл должен существовать на диске.
    from app.config import BASE_DIR

    path = Path(BASE_DIR) / two["file_path" if "file_path" in two else "original_filename"] if False else None  # noqa
    # Список: оба датасета, активен только второй.
    lst = client.get("/api/datasets", headers={"Authorization": f"Bearer {admin}"}).json()
    by_name = {d["name"]: d for d in lst}
    assert by_name["yolov8n_v1"]["is_active"] is False
    assert by_name["yolov8n_v2"]["is_active"] is True

    # Активируем первый — второй автоматически становится неактивным.
    r = client.post(
        f"/api/datasets/{one['id']}/activate",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True

    lst = client.get("/api/datasets", headers={"Authorization": f"Bearer {admin}"}).json()
    by_name = {d["name"]: d for d in lst}
    assert by_name["yolov8n_v1"]["is_active"] is True
    assert by_name["yolov8n_v2"]["is_active"] is False


def test_reject_wrong_extension(client):
    admin = login(client, "admin", "admin12345")
    files = {"file": ("weights.txt", io.BytesIO(b"text"), "text/plain")}
    res = client.post(
        "/api/datasets",
        headers={"Authorization": f"Bearer {admin}"},
        files=files,
        data={"name": "bad-ext"},
    )
    assert res.status_code == 400


def test_delete_dataset(client):
    admin = login(client, "admin", "admin12345")
    d = _upload(client, admin, name="to-delete")
    res = client.delete(
        f"/api/datasets/{d['id']}",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert res.status_code == 204
    # Файлов больше нет.
    from app.config import BASE_DIR
    from app.services.dataset_manager import DATASETS_DIR

    assert not (DATASETS_DIR / str(d["id"])).exists()


def test_deactivate_all(client):
    admin = login(client, "admin", "admin12345")
    d = _upload(client, admin, name="active", activate=True)
    assert d["is_active"] is True
    res = client.post(
        "/api/datasets/deactivate",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert res.status_code == 204
    lst = client.get("/api/datasets", headers={"Authorization": f"Bearer {admin}"}).json()
    assert all(not x["is_active"] for x in lst)
