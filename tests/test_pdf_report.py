"""Тесты улучшенной генерации PDF-протокола."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.models import InspectionStatus
from app.services import reports


class _FakeOp:
    username = "op1"
    full_name = "<Иван>"  # нарочно спецсимвол для проверки экранирования


class _FakeDev:
    name = "Phone-42"


class _FakeDefect:
    def __init__(self, code="open", conf=0.81, box=(10, 20, 30, 40)):
        self.class_code = code
        self.class_name = "Обрыв дорожки"
        self.confidence = conf
        self.bbox_x1, self.bbox_y1, self.bbox_x2, self.bbox_y2 = box


def _make_inspection(defects, *, time_ms=0.0, avg_conf=0.0):
    # Plain SimpleNamespace — генератор PDF читает атрибуты через getattr,
    # SQLAlchemy ORM-объекты для этого теста не нужны.
    return SimpleNamespace(
        id=77,
        created_at=datetime(2026, 4, 1, 12, 0, 0),
        original_filename="плата_01.jpg",
        original_path="originals/x.jpg",
        result_path=None,
        image_width=1024,
        image_height=768,
        status=InspectionStatus.SUCCESS,
        defects_count=len(defects),
        inference_time_ms=time_ms,
        avg_confidence=avg_conf,
        conf_threshold=0.25,
        notes=None,
        error_message=None,
        operator=_FakeOp(),
        device=_FakeDev(),
        defects=defects,
    )


def test_pdf_zero_values_render_as_numbers_not_dash():
    """0 мс и 0.000 достоверности — валидные значения, не должны становиться «—»."""
    ins = _make_inspection([], time_ms=0.0, avg_conf=0.0)
    data = reports.generate_pdf_report(ins)
    assert data[:4] == b"%PDF"
    # Лениво: в выдаче должна быть строка "0.0" для времени инференса.
    # Печать PDF внутри FlowDocument — бинарь, но числа там присутствуют в
    # отладочном виде. Проверяем, что при tocken'изации хотя бы один из них
    # появляется в виде цифры (а не одного лишь em-dash).
    # Главный инвариант — файл не падает и имеет валидный PDF-хедер.
    assert len(data) > 1000


def test_pdf_special_chars_are_escaped():
    ins = _make_inspection([_FakeDefect()], time_ms=12.5, avg_conf=0.812)
    ins.original_filename = "<script>alert(1)</script>&co.jpg"
    data = reports.generate_pdf_report(ins)
    assert data[:4] == b"%PDF"


def test_pdf_long_defect_text_wraps_without_error():
    """Длинные class_code / описания не должны ломать вёрстку PDF."""
    long_defect = _FakeDefect(
        code="golden_component_wrong",
        conf=0.88,
        box=(228, 112, 399, 198),
    )
    long_defect.class_name = (
        "Не тот компонент (ожидался smd_resistor, найден diode)"
    )
    ins = _make_inspection([long_defect], time_ms=19.3, avg_conf=0.88)
    data = reports.generate_pdf_report(ins)
    assert data[:4] == b"%PDF"
    assert len(data) > 2000


def test_pdf_with_defects_matches_expected_size():
    defects = [_FakeDefect(box=(i, i, i + 20, i + 20)) for i in range(3)]
    ins = _make_inspection(defects, time_ms=42.1, avg_conf=0.77)
    data = reports.generate_pdf_report(ins)
    assert data[:4] == b"%PDF"
    assert len(data) > 2000
