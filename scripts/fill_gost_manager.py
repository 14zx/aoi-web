"""Create AOI-Web manager manual from an existing GOST template.

On Desktop there are only GOST templates 33 (programmer) and 34 (operator).
If the manager template (35) is missing, we reuse template 34 and switch it to
\"Руководство руководителя\".
"""

from __future__ import annotations

import sys
from pathlib import Path

import win32com.client  # type: ignore


DESK = Path(r"c:\\Users\\Neizy\\Desktop")
TEMPLATE_OPER = DESK / "34 Руководство оператора.dot"

OUT = DESK / "АОИ.01.35 01 — Руководство руководителя (АОИ-Web).docx"

TITLE = "АОИ-WEB — ПРОГРАММНАЯ ЧАСТЬ ПАК АВТОМАТИЧЕСКОЙ ОПТИЧЕСКОЙ ИНСПЕКЦИИ"


OPER_ANNO_OLD = (
    "[[[«Mem.ехе», предназначенной для очистки и дефрагментации оперативной памяти ПК через заданные интервалы времени.]]]"
)

# In the original templates the annotation sometimes has a line-broken form.
OPER_ANNO_OLD_ALT = (
    "[[[«Mem.ехе», предназначенной для\n"
    "очистки и дефрагментации оперативной памяти ПК через заданные интервалы\n"
    "времени.]]]"
)

OPER_ANNO_NEW = (
    "«АОИ-Web» (обозначение: АОИ.01), предназначенной для проведения автоматической "
    "оптической инспекции печатных узлов руководителем: контроль общего процесса, "
    "просмотр сводных журналов и статистики, управление датасетами и устройствами "
    "в пределах прав доступа."
)


MANAGER_TEXTS = [
    "«АОИ-Web» предназначена для автоматической оптической инспекции печатных узлов: "
    "руководитель контролирует общий процесс и результаты детекции.",
    "Эксплуатация руководителем: просмотр общего журнала и статистики, управление "
    "датасетами и устройствами (в пределах прав), контроль параметров анализа.",
    "Состав функций: просмотр сводных данных, управление датасетами (активация), "
    "просмотр устройств и пользователей, организация повторных проверок.",
    "Функция (такая-то): раздел «Статистика» — просмотр метрик по дефектам и динамики "
    "по времени, сравнение результатов инспекций.",
    "Функция (этакая): раздел «Датасеты» и «Устройства» — выбор активного датасета/"
    "модели, просмотр состояния устройств и их параметров регистрации.",
    "Журнал инспекций: просмотр общих записей, статусов инспекций и результатов.",
    "ПК/планшет с браузером; доступ по HTTP/HTTPS в локальной сети до сервера АОИ-Web.",
    "Chrome/Edge/Firefox; на телефоне — сценарий /phone (если руководитель контролирует "
    "съёмку), при необходимости разрешить доступ к камере.",
    "Требования к персоналу: руководитель должен уметь работать с веб-интерфейсом, "
    "понимать где настраиваются датасеты/модели и интерпретировать сводные отчёты.",
    "Открыть адрес сервера (например https://192.168.x.x:8000/), войти под учётной "
    "записью руководителя.",
    "Вкладки «Статистика», «Датасеты», «Устройства», «Пользователи» — выполнять "
    "действия только в пределах предоставленных прав.",
    "Открыть протоколы инспекций, при необходимости сформировать требования/замечания "
    "оператору для повторной проверки отдельных плат.",
    "Экспорт сводных отчётов/протоколов (CSV/PDF) при наличии прав доступа.",
    "«Недостаточно прав» — выполнить доступные действия либо обратиться к администратору "
    "для операций, требующих более высокого уровня доступа.",
    "«Ошибка загрузки данных/датасета» — проверить доступность сервера, целостность "
    "входных файлов и повторить попытку.",
]


def replace_all(doc, find: str, repl: str) -> None:
    rng = doc.Content
    rng.Find.ClearFormatting()
    rng.Find.Replacement.ClearFormatting()
    rng.Find.Execute(
        FindText=find,
        MatchCase=False,
        MatchWholeWord=False,
        Forward=True,
        Wrap=1,
        Format=False,
        ReplaceWith=repl,
        Replace=2,
    )


def replace_first(doc, find: str, repl: str) -> None:
    rng = doc.Content
    rng.Find.ClearFormatting()
    rng.Find.Replacement.ClearFormatting()
    ok = rng.Find.Execute(
        FindText=find,
        MatchCase=False,
        MatchWholeWord=True,
        Forward=True,
        Wrap=0,
        Format=False,
        ReplaceWith=repl,
        Replace=1,
    )
    if not ok:
        raise RuntimeError(f"Placeholder not found: {find!r}")


def main() -> None:
    if not TEMPLATE_OPER.exists():
        raise FileNotFoundError(str(TEMPLATE_OPER))

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0

    doc = None
    try:
        doc = word.Documents.Open(str(TEMPLATE_OPER), ReadOnly=False, Visible=False)

        replace_all(doc, "ПРОГРАММА ОЧИСТКИ ОПЕРАТИВНОЙ ПАМЯТИ", TITLE)
        replace_all(doc, "А.В.00001-01 34 01-ЛУ", "АОИ.01.35 01-ЛУ")
        replace_all(doc, "А.В.00001-01 34 01", "АОИ.01.35 01")
        replace_all(doc, "Руководство оператора", "Руководство руководителя")
        replace_all(doc, "Mem.ехе", "АОИ-Web")
        replace_all(doc, "Mem.exe", "АОИ-Web")
        replace_all(
            doc,
            "очистки и дефрагментации оперативной памяти ПК через заданные интервалы времени",
            "автоматической оптической инспекции печатных узлов через web-интерфейс",
        )

        # Replace annotation (try both variants)
        replace_all(doc, OPER_ANNO_OLD, OPER_ANNO_NEW)
        replace_all(doc, OPER_ANNO_OLD_ALT, OPER_ANNO_NEW)

        for t in MANAGER_TEXTS:
            replace_first(doc, "Текст", t)

        doc.SaveAs2(str(OUT), FileFormat=16)  # .docx
        print(f"Saved: {OUT}")
        doc.Close(False)
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        word.Quit()


if __name__ == "__main__":
    sys.exit(main())

