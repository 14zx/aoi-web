"""Default class semantics for portable (dataset 7 YOLO + golden-board defect codes)."""

from __future__ import annotations

from app.database import SessionLocal
from app.services.class_semantics import save_mappings

# YOLO names from models/datasets/7/weights.pt (component detection).
_COMPONENTS: list[tuple[str, str]] = [
    ("smd_capacitor", "SMD конденсатор"),
    ("smd_CAPACITOR", "SMD конденсатор"),
    ("diode", "Диод"),
    ("DIODE", "Диод"),
    ("ec", "Электролитический конденсатор"),
    ("EC", "Электролитический конденсатор"),
    ("ic", "Микросхема"),
    ("IC", "Микросхема"),
    ("led", "Светодиод"),
    ("LED", "Светодиод"),
    ("smd_resistor", "SMD резистор"),
    ("smd_RESISTOR", "SMD резистор"),
    ("scapacitor", "Конденсатор (крупный)"),
    ("SCAPACITOR", "Конденсатор (крупный)"),
    ("zener", "Стабилитрон"),
    ("ZENER", "Стабилитрон"),
    ("smd_pad", "SMD площадка"),
]

# Logical assembly defects (golden board / pipeline), not YOLO head output.
_DEFECTS: list[tuple[str, str]] = [
    ("golden_component_missing", "Нет элемента (эталон)"),
    ("golden_component_wrong", "Не тот элемент (эталон)"),
    ("golden_polarity_wrong", "Неверная полярность"),
    ("placement_tilt", "Смещение / ориентация"),
    ("component_missing", "Отсутствие компонента"),
    ("component_misaligned", "Смещение компонента"),
]


def main() -> int:
    mappings: dict[str, dict] = {}
    for code, label in _COMPONENTS:
        mappings[code] = {"kind": "component", "label": label, "review_required": False}
    for code, label in _DEFECTS:
        mappings[code] = {"kind": "defect", "label": label, "review_required": True}

    with SessionLocal() as db:
        save_mappings(db, mappings, updated_by=None)
        db.commit()
    print(f"OK: class_semantics seeded ({len(mappings)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
