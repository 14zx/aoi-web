"""Общие фикстуры pytest.

Каждый тест работает в изолированном временном каталоге с in-memory SQLite,
чтобы не затрагивать реальную БД или хранилище.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# ВАЖНО: переменные окружения и перенаправление путей должны быть выставлены
# ДО любого импорта ``app.*`` — иначе ``Settings`` и SQLAlchemy-``engine``
# закэшируются со значениями реальной среды и тесты начнут писать в
# рабочую БД и каталоги проекта. Session-scope autouse фикстура для этого не
# годится (она срабатывает уже после коллекции модулей тестов).
_TEST_TMP = Path(tempfile.mkdtemp(prefix="aoi-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_TMP / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_TEST_TMP / "storage")
os.environ["LOG_FILE"] = str(_TEST_TMP / "logs" / "test.log")
os.environ["SECRET_KEY"] = "unit-test-secret-key-must-be-at-least-32-chars"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin12345"
os.environ["MODEL_WEIGHTS_PATH"] = str(_TEST_TMP / "no-weights.pt")
# Каталог для тестовых весов датасетов — должен быть вне репозитория.
_TEST_DATASETS_DIR = _TEST_TMP / "datasets"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _redirect_datasets_dir() -> None:
    """Подменяет ``dataset_manager.DATASETS_DIR`` на временный каталог."""
    from app.services import dataset_manager

    dataset_manager.DATASETS_DIR = _TEST_DATASETS_DIR
    dataset_manager.DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture()
def client():
    """TestClient FastAPI с созданной схемой и предзаведённым админом."""
    from fastapi.testclient import TestClient

    from app.core.security import hash_password
    from app.database import Base, SessionLocal, engine
    from app.main import app
    from app.models import User, UserRole

    from app.services.command_queue import command_queue
    from app.services.hardware_gateway import reset_hardware_gateway_for_tests
    from app.services.stream_store import stream_store

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    stream_store.clear()
    command_queue.clear()
    reset_hardware_gateway_for_tests()

    with SessionLocal() as db:
        db.add(
            User(
                username="admin",
                full_name="Admin",
                hashed_password=hash_password("admin12345"),
                role=UserRole.ADMIN,
                is_active=True,
            )
        )
        db.add(
            User(
                username="manager1",
                full_name="Руководитель Один",
                hashed_password=hash_password("manager12345"),
                role=UserRole.MANAGER,
                is_active=True,
            )
        )
        db.add(
            User(
                username="operator1",
                full_name="Оператор Один",
                hashed_password=hash_password("operator12345"),
                role=UserRole.OPERATOR,
                is_active=True,
            )
        )
        db.commit()

    with TestClient(app) as c:
        yield c


def login(client, username: str, password: str) -> str:
    res = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def user_id(client, token: str, username: str) -> int:
    users = client.get("/api/users", headers={"Authorization": f"Bearer {token}"}).json()
    match = next(u for u in users if u["username"] == username)
    return match["id"]


def designate_device(client, manager_token: str, device_id: int, operator_id: int | None) -> dict:
    res = client.patch(
        f"/api/devices/{device_id}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"designated_operator_id": operator_id},
    )
    assert res.status_code == 200, res.text
    return res.json()


def designate_golden_board(client, manager_token: str, profile_id: int, operator_id: int | None) -> dict:
    res = client.patch(
        f"/api/golden-boards/{profile_id}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"designated_operator_id": operator_id},
    )
    assert res.status_code == 200, res.text
    return res.json()
