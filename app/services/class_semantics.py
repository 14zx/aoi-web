"""Сохранение в БД семантики классов YOLO: компонент / дефект / игнор, подписи, обязательная проверка."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from sqlalchemy.orm import Session

from ..models import AppSetting

logger = logging.getLogger(__name__)

SETTING_KEY = "class_semantics"

# kind: component | defect | ignore
_DEFAULT_ENTRY: dict[str, Any] = {
    "kind": "defect",
    "label": "",
    "review_required": True,
}


def _normalize_entry(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return dict(_DEFAULT_ENTRY)
    kind = str(raw.get("kind") or "defect").lower()
    if kind not in ("component", "defect", "ignore"):
        kind = "defect"
    return {
        "kind": kind,
        "label": str(raw.get("label") or "")[:128],
        "review_required": bool(raw.get("review_required", True)),
    }


def lookup_semantic_entry(
    class_code: str, mappings: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Ищет правило по точному коду или без учёта регистра (IC vs ic)."""
    if not class_code or not mappings:
        return None
    key = str(class_code).strip()
    if key in mappings:
        v = mappings[key]
        return v if isinstance(v, dict) else None
    low = key.lower()
    for k, v in mappings.items():
        if str(k).strip().lower() == low and isinstance(v, dict):
            return v
    return None


SemanticKind = Literal["defect", "component", "ignore"]


def semantic_kind_for_class(
    class_code: str, mappings: dict[str, dict[str, Any]]
) -> SemanticKind:
    """Вид объекта для UI и счётчиков; по умолчанию «дефект», если правило не задано."""
    if str(class_code).strip() in ("placement_tilt", "solder_bridge"):
        return "defect"
    if str(class_code).strip() in (
        "golden_component_missing",
        "golden_component_wrong",
        "golden_polarity_wrong",
    ):
        return "defect"
    m = lookup_semantic_entry(class_code, mappings)
    if not m:
        from .detector import registry_class_is_defect

        return "component" if not registry_class_is_defect(class_code) else "defect"
    kind = str(m.get("kind") or "defect").lower()
    if kind not in ("component", "defect", "ignore"):
        return "defect"
    return kind  # type: ignore[return-value]


def counts_as_protocol_defect(
    class_code: str, mappings: dict[str, dict[str, Any]]
) -> bool:
    """Учитывается в ``Inspection.defects_count`` до ручной проверки оператором."""
    return semantic_kind_for_class(class_code, mappings) == "defect"


def load_mappings(db: Session) -> dict[str, dict[str, Any]]:
    """Возвращает словарь class_code -> {kind, label, review_required}."""
    row = db.get(AppSetting, SETTING_KEY)
    if row is None or not row.value.strip():
        return {}
    try:
        data = json.loads(row.value)
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for k, v in data.items():
            code = str(k).strip()
            if not code:
                continue
            if isinstance(v, dict):
                out[code] = _normalize_entry(v)
        return out
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Повреждён JSON class_semantics: %s", exc)
        return {}


def save_mappings(
    db: Session,
    mappings: dict[str, dict[str, Any]],
    *,
    updated_by: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Сохраняет карту; возвращает нормализованную."""
    normalized: dict[str, dict[str, Any]] = {}
    for k, v in mappings.items():
        code = str(k).strip()
        if not code:
            continue
        normalized[code] = _normalize_entry(v if isinstance(v, dict) else {})

    payload = json.dumps(normalized, ensure_ascii=False)
    existing = db.get(AppSetting, SETTING_KEY)
    if existing is None:
        db.add(AppSetting(key=SETTING_KEY, value=payload, updated_by=updated_by))
    else:
        existing.value = payload
        existing.updated_by = updated_by
    return normalized


def classify_for_review(class_code: str, mappings: dict[str, dict[str, Any]]) -> bool:
    """Нужна ли ручная оценка «брак / не брак» для этого кода (по умолчанию да)."""
    m = lookup_semantic_entry(class_code, mappings)
    if not m:
        return True
    kind = m.get("kind", "defect")
    if kind == "ignore":
        return False
    if kind == "component":
        return bool(m.get("review_required", False))
    return bool(m.get("review_required", True))


def is_component_class(class_code: str, mappings: dict[str, dict[str, Any]]) -> bool:
    return semantic_kind_for_class(class_code, mappings) == "component"


def auto_real_defect_if_unreviewed(
    class_code: str, mappings: dict[str, dict[str, Any]]
) -> bool | None:
    """Если оператор не давал оценку: авто-вердикт или ``None`` (нужен человек)."""
    m = lookup_semantic_entry(class_code, mappings)
    if not m:
        return None
    kind = str(m.get("kind") or "defect")
    if kind == "ignore":
        return False
    if kind == "component":
        return False
    if kind == "defect":
        if bool(m.get("review_required", True)):
            return None
        return True
    return None
