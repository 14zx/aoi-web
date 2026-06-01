"""Формирование протоколов инспекции в форматах PDF и CSV (ТЗ Ф10)."""

from __future__ import annotations

import csv
import html
import io
import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..config import settings
from ..models.inspection import Inspection


log = logging.getLogger("aoi.reports")


# ---------------------------------------------------------------------------
# Регистрируем шрифт с кириллицей для PDF.
# ---------------------------------------------------------------------------
_FONT_NAME = "AOIFont"
_FONT_BOLD = "AOIFont-Bold"
_FONT_REGISTERED = False


def _matplotlib_font(stem: str) -> str | None:
    """Возвращает путь к TTF-шрифту, поставляемому вместе с matplotlib.

    matplotlib — транзитивная зависимость ultralytics, почти всегда есть на
    проде. Использование её встроенного DejaVu гарантирует кириллицу без
    привязки к ОС.
    """
    try:
        import matplotlib
    except Exception:
        return None
    root = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
    p = root / stem
    return str(p) if p.exists() else None


def _first_existing(paths: list[str | None]) -> str | None:
    for p in paths:
        if p and Path(p).exists():
            return p
    return None


def _ensure_font() -> str:
    """Регистрирует TTF-шрифт, поддерживающий кириллицу.

    Порядок поиска: matplotlib-DejaVu (кроссплатформенно), системные пути
    Windows/Linux/macOS. В самом крайнем случае — Helvetica (без кириллицы).
    """
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return _FONT_NAME

    reg_candidates = [
        _matplotlib_font("DejaVuSans.ttf"),
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    bold_candidates = [
        _matplotlib_font("DejaVuSans-Bold.ttf"),
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    reg_path = _first_existing(reg_candidates)
    if reg_path is None:
        log.warning("Не найден TTF-шрифт с кириллицей, использую Helvetica (могут быть артефакты)")
        return "Helvetica"
    try:
        pdfmetrics.registerFont(TTFont(_FONT_NAME, reg_path))
    except Exception:
        log.exception("Не удалось зарегистрировать TTF %s", reg_path)
        return "Helvetica"

    bold_path = _first_existing(bold_candidates) or reg_path
    try:
        pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold_path))
    except Exception:
        # Без жирного шрифта просто продолжаем с тем же базовым.
        try:
            pdfmetrics.registerFont(TTFont(_FONT_BOLD, reg_path))
        except Exception:
            pass
    _FONT_REGISTERED = True
    return _FONT_NAME


def _fmt_number(value, fmt: str, dash: str = "—") -> str:
    """Форматирует число или возвращает прочерк для None.

    Важно: 0 — валидное число (например, 0 дефектов), его нельзя отбрасывать
    через truthy-проверку. Раньше в PDF 0 показывался как «—».
    """
    if value is None:
        return dash
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return dash


def _escape(text) -> str:
    return html.escape("" if text is None else str(text), quote=False)


def _break_long_tokens(text: str) -> str:
    """Разрешает перенос длинных идентификаторов (golden_component_wrong)."""
    s = _escape(text)
    if len(s) <= 18:
        return s
    return s.replace("_", "_\u200b")


def _pdf_cell(
    text,
    styles,
    *,
    font_name: str,
    font_size: int = 8,
    bold: bool = False,
    break_tokens: bool = False,
) -> Paragraph:
    body = _break_long_tokens(text) if break_tokens else _escape(text)
    style = ParagraphStyle(
        f"pdf_cell_{font_size}_{'b' if bold else 'r'}",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=font_size,
        leading=font_size + 2,
        wordWrap="LTR",
    )
    return Paragraph(body, style)


def _verdict_label(defect) -> str:
    """Человекочитаемый вердикт оператора для отчёта."""
    if not getattr(defect, "is_reviewed", False):
        return "не проверен"
    return "подтверждён" if defect.is_real_defect else "не дефект"


def generate_csv_report(inspection: Inspection) -> bytes:
    """Формирует CSV-протокол одной инспекции."""
    defects = list(inspection.defects)
    confirmed = [
        d for d in defects
        if getattr(d, "is_reviewed", False) and d.is_real_defect
    ]
    rejected = [
        d for d in defects
        if getattr(d, "is_reviewed", False) and not d.is_real_defect
    ]

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Протокол инспекции №", inspection.id])
    writer.writerow(["Оператор", inspection.operator.username if inspection.operator else ""])
    writer.writerow(["Файл", inspection.original_filename])
    writer.writerow([
        "Модель платы",
        getattr(inspection, "board_model", None) or "—",
    ])
    writer.writerow(["Дата/время", inspection.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow(["Статус", inspection.status.value])
    if inspection.reviewed_at:
        writer.writerow([
            "Проверено оператором",
            inspection.reviewed_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])
        writer.writerow(["Подтверждено дефектов", len(confirmed)])
        writer.writerow(["Отклонено (не брак)", len(rejected)])
    else:
        writer.writerow(["Проверено оператором", "— (не проверено вручную)"])
    writer.writerow(["Модель обнаружила", len(defects)])
    writer.writerow(["Итоговое кол-во дефектов", inspection.defects_count])
    writer.writerow(["Время инференса, мс", f"{inspection.inference_time_ms or 0:.2f}"])
    writer.writerow([])
    writer.writerow([
        "#", "Класс", "Описание", "Достоверность",
        "Оценка оператора", "x1", "y1", "x2", "y2",
    ])
    for i, d in enumerate(defects, start=1):
        writer.writerow(
            [
                i,
                d.class_code,
                d.class_name,
                f"{d.confidence:.3f}",
                _verdict_label(d),
                d.bbox_x1,
                d.bbox_y1,
                d.bbox_x2,
                d.bbox_y2,
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def generate_pdf_report(inspection: Inspection) -> bytes:
    """Формирует PDF-протокол одной инспекции (ReportLab).

    Улучшения по сравнению с прошлой версией:
      * Шрифт с кириллицей ищется в matplotlib (кроссплатформенно), а не
        только в системных папках — протокол корректно собирается и на
        Linux-контейнере без arial.ttf.
      * 0 в полях «время инференса» и «средняя достоверность» отображается
        как 0, а не прочерк.
      * Параграфы экранируются (имя файла со спецсимволами больше не ломает
        XML-парсер ReportLab).
      * Изображение не растягивается: ограничивается и по ширине, и по
        высоте, чтобы не вылезать за страницу.
      * В таблицу добавлены строки про устройство и примечания.
    """
    font = _ensure_font()
    font_bold = _FONT_BOLD if _FONT_REGISTERED else font
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Протокол АОИ №{inspection.id}",
        author=settings.app_name,
    )

    styles = getSampleStyleSheet()
    for style_name in ("Normal", "Title", "Heading2", "Heading3"):
        styles[style_name].fontName = font
    styles["Title"].fontName = font_bold
    styles["Heading3"].fontName = font_bold
    styles["Title"].fontSize = 16
    styles["Title"].leading = 20

    story: list = []
    story.append(Paragraph(f"Протокол инспекции № {inspection.id}", styles["Title"]))
    story.append(
        Paragraph(
            f"{_escape(settings.app_name)} ({_escape(settings.app_code)})",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 4 * mm))

    operator = inspection.operator
    device = getattr(inspection, "device", None)
    defects_all = list(inspection.defects)
    reviewed_at = getattr(inspection, "reviewed_at", None)
    confirmed_all = [
        d for d in defects_all
        if getattr(d, "is_reviewed", False) and d.is_real_defect
    ]
    rejected_all = [
        d for d in defects_all
        if getattr(d, "is_reviewed", False) and not d.is_real_defect
    ]

    meta_rows = [
        ["Дата и время", inspection.created_at.strftime("%Y-%m-%d %H:%M:%S")],
        ["Оператор", (operator.username if operator else None) or "—"],
        ["ФИО оператора", (operator.full_name if operator else None) or "—"],
        ["Устройство", (device.name if device else None) or "—"],
        ["Модель платы", getattr(inspection, "board_model", None) or "—"],
        ["Файл изображения", inspection.original_filename or "—"],
        [
            "Разрешение",
            f"{inspection.image_width or '?'} × {inspection.image_height or '?'}",
        ],
        ["Статус", inspection.status.value],
        [
            "Порог достоверности",
            _fmt_number(inspection.conf_threshold, "{:.2f}"),
        ],
        ["Обнаружено моделью", str(len(defects_all))],
    ]
    if reviewed_at is not None:
        meta_rows.append([
            "Проверено оператором",
            reviewed_at.strftime("%Y-%m-%d %H:%M:%S"),
        ])
        meta_rows.append(["Подтверждено (брак)", str(len(confirmed_all))])
        meta_rows.append(["Отклонено (не брак)", str(len(rejected_all))])
    else:
        meta_rows.append([
            "Проверено оператором",
            "— (автоматический результат без ручной проверки)",
        ])
    meta_rows.append(["Итого дефектов в протоколе", str(inspection.defects_count or 0)])
    meta_rows.append([
        "Время инференса, мс",
        _fmt_number(inspection.inference_time_ms, "{:.1f}"),
    ])
    meta_rows.append([
        "Средняя достоверность",
        _fmt_number(inspection.avg_confidence, "{:.3f}"),
    ])
    if inspection.notes:
        meta_rows.append(["Примечание", inspection.notes])
    if inspection.status.value != "success" and inspection.error_message:
        meta_rows.append(["Ошибка", inspection.error_message])
    meta_table = Table(meta_rows, colWidths=[55 * mm, 120 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTNAME", (0, 0), (0, -1), font_bold),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 6 * mm))

    # Изображение с результатом (если сохранено).
    image_block_added = False
    if inspection.result_path:
        img_path = settings.storage_dir / inspection.result_path
        if img_path.exists():
            try:
                max_w = 175 * mm
                max_h = 130 * mm  # Ограничиваем высоту, чтобы не съехать на 2-ю страницу
                w = inspection.image_width or 0
                h = inspection.image_height or 0
                if w > 0 and h > 0:
                    scale = min(max_w / w, max_h / h)
                    iw, ih = w * scale, h * scale
                else:
                    iw, ih = max_w, max_w * 0.75
                story.append(Paragraph("Визуализация результата:", styles["Heading3"]))
                story.append(Image(str(img_path), width=iw, height=ih))
                story.append(Spacer(1, 4 * mm))
                image_block_added = True
            except Exception:
                log.exception("Не удалось вставить изображение результата в PDF")
    if not image_block_added:
        story.append(
            Paragraph(
                "<i>Изображение результата недоступно.</i>",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 4 * mm))

    # Таблица дефектов — Paragraph в ячейках, чтобы длинный текст переносился.
    story.append(Paragraph("Список обнаруженных дефектов:", styles["Heading3"]))
    usable_w = doc.width
    fixed = (8 + 24 + 16 + 22 + 42) * mm
    desc_w = max(50 * mm, usable_w - fixed)
    col_widths = [8 * mm, 24 * mm, desc_w, 16 * mm, 22 * mm, 42 * mm]
    header = [
        _pdf_cell("#", styles, font_name=font_bold, font_size=8, bold=True),
        _pdf_cell("Класс", styles, font_name=font_bold, font_size=8, bold=True),
        _pdf_cell("Описание", styles, font_name=font_bold, font_size=8, bold=True),
        _pdf_cell("Достов.", styles, font_name=font_bold, font_size=8, bold=True),
        _pdf_cell("Оценка", styles, font_name=font_bold, font_size=8, bold=True),
        _pdf_cell("Координаты", styles, font_name=font_bold, font_size=8, bold=True),
    ]
    rows = [header]
    rejected_row_indices: list[int] = []
    for i, d in enumerate(inspection.defects, start=1):
        verdict = _verdict_label(d)
        rows.append(
            [
                _pdf_cell(str(i), styles, font_name=font, font_size=8),
                _pdf_cell(d.class_code, styles, font_name=font, font_size=8, break_tokens=True),
                _pdf_cell(d.class_name, styles, font_name=font, font_size=8),
                _pdf_cell(f"{d.confidence:.2f}", styles, font_name=font, font_size=8),
                _pdf_cell(verdict, styles, font_name=font, font_size=8),
                _pdf_cell(
                    f"({d.bbox_x1}, {d.bbox_y1}, {d.bbox_x2}, {d.bbox_y2})",
                    styles,
                    font_name=font,
                    font_size=7,
                    break_tokens=True,
                ),
            ]
        )
        if getattr(d, "is_reviewed", False) and not d.is_real_defect:
            rejected_row_indices.append(i)
    if len(rows) == 1:
        rows.append(
            [
                _pdf_cell("—", styles, font_name=font, font_size=8),
                _pdf_cell("", styles, font_name=font, font_size=8),
                _pdf_cell("Дефектов не обнаружено", styles, font_name=font, font_size=8),
                _pdf_cell("", styles, font_name=font, font_size=8),
                _pdf_cell("", styles, font_name=font, font_size=8),
                _pdf_cell("", styles, font_name=font, font_size=8),
            ]
        )
    defect_table = Table(rows, colWidths=col_widths, repeatRows=1)
    table_cmds = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("ALIGN", (4, 0), (4, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    # Визуально выделяем отклонённые дефекты: красноватый фон + перечёркнутый текст.
    for r in rejected_row_indices:
        table_cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#fee2e2")))
        table_cmds.append(("TEXTCOLOR", (0, r), (-1, r), colors.HexColor("#991b1b")))
        table_cmds.append(("FONTNAME", (0, r), (-1, r), font))
    defect_table.setStyle(TableStyle(table_cmds))
    story.append(defect_table)

    if rejected_all:
        story.append(Spacer(1, 4 * mm))
        story.append(
            Paragraph(
                "<i>Строки, отмеченные красным, — срабатывания модели, "
                "которые оператор при ручной проверке признал не дефектами. "
                "Они сохранены в протоколе для аудита, но не учтены в общем "
                "количестве дефектов и не используются для дообучения модели "
                "как примеры дефектов соответствующего класса.</i>",
                styles["Normal"],
            )
        )

    story.append(Spacer(1, 10 * mm))
    story.append(
        Paragraph(
            f"Протокол сформирован автоматически {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.",
            styles["Normal"],
        )
    )

    doc.build(story)
    return buf.getvalue()
