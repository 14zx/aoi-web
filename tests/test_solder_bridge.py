"""Тесты эвристического поиска перемычек припоя (solder_bridge)."""

from __future__ import annotations

import numpy as np

from app.database import SessionLocal
from app.services.class_semantics import save_mappings
from app.services.detector import DetectedDefect
from app.services.solder_bridge_check import CLASS_SOLDER_BRIDGE, find_solder_bridges


def _component(x1: int, y1: int, x2: int, y2: int, code: str = "ic") -> DetectedDefect:
    return DetectedDefect(
        class_code=code,
        class_name=code,
        confidence=0.9,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def _mark_component(db, code: str = "ic") -> None:
    save_mappings(db, {code: {"kind": "component", "label": code, "review_required": False}})
    db.commit()


def test_bright_gap_detected_as_bridge(client) -> None:  # noqa: ARG001 (нужна схема БД)
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    # Два соседних вывода и яркий металлический мостик припоя между ними.
    a = _component(40, 80, 90, 120)
    b = _component(110, 80, 160, 120)
    img[80:120, 40:160] = (245, 245, 245)  # яркий малонасыщенный = припой

    with SessionLocal() as db:
        _mark_component(db)
        out = find_solder_bridges(img, [a, b], db)

    codes = [d.class_code for d in out]
    assert CLASS_SOLDER_BRIDGE in codes


def test_dark_gap_not_flagged(client) -> None:  # noqa: ARG001
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    a = _component(40, 80, 90, 120)
    b = _component(110, 80, 160, 120)
    # Пады яркие, но зазор тёмный (фон платы) — мостика нет.
    img[80:120, 40:90] = (245, 245, 245)
    img[80:120, 110:160] = (245, 245, 245)

    with SessionLocal() as db:
        _mark_component(db)
        out = find_solder_bridges(img, [a, b], db)

    assert all(d.class_code != CLASS_SOLDER_BRIDGE for d in out)


def test_far_apart_components_not_flagged(client) -> None:  # noqa: ARG001
    img = np.full((200, 300, 3), 245, dtype=np.uint8)  # всё яркое
    a = _component(10, 80, 60, 120)
    b = _component(230, 80, 280, 120)  # зазор много больше max_gap

    with SessionLocal() as db:
        _mark_component(db)
        out = find_solder_bridges(img, [a, b], db)

    assert all(d.class_code != CLASS_SOLDER_BRIDGE for d in out)


def test_disabled_setting_skips(client, monkeypatch) -> None:  # noqa: ARG001
    from app.config import settings

    monkeypatch.setattr(settings, "solder_bridge_check_enabled", False)
    img = np.full((200, 200, 3), 245, dtype=np.uint8)
    a = _component(40, 80, 90, 120)
    b = _component(110, 80, 160, 120)

    with SessionLocal() as db:
        _mark_component(db)
        out = find_solder_bridges(img, [a, b], db)

    assert all(d.class_code != CLASS_SOLDER_BRIDGE for d in out)
