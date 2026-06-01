"""Fill GOST .dot manuals (programmer + operator) for AOI-Web."""

from __future__ import annotations

import sys
from pathlib import Path

import win32com.client  # type: ignore

DESK = Path(r"c:\Users\Neizy\Desktop")
PROG_TEMPLATE = DESK / "33 Руководство программиста.dot"
OPER_TEMPLATE = DESK / "34 Руководство оператора.dot"
PROG_OUT = DESK / "АОИ.01.33 01 — Руководство программиста (АОИ-Web).docx"
OPER_OUT = DESK / "АОИ.01.34 01 — Руководство оператора (АОИ-Web).docx"

TITLE = "АОИ-WEB — ПРОГРАММНАЯ ЧАСТЬ ПАК АВТОМАТИЧЕСКОЙ ОПТИЧЕСКОЙ ИНСПЕКЦИИ"

PROG_ANNO_OLD = (
    "[[[«Mem.ехе», предназначенной для очистки\n"
    "и дефрагментации оперативной памяти ПК через заданные интервалы времени.]]]"
)
PROG_ANNO_NEW = (
    "«АОИ-Web» (обозначение: АОИ.01), предназначенной для автоматической "
    "оптической инспекции печатных узлов с использованием веб-интерфейса, "
    "нейросетевой детекции (YOLOv8) и опционального управления подсветкой WLED."
)

OPER_ANNO_OLD = (
    "[[[«Mem.ехе», предназначенной для\n"
    "очистки и дефрагментации оперативной памяти ПК через заданные интервалы\n"
    "времени.]]]"
)
OPER_ANNO_NEW = (
    "«АОИ-Web» (обозначение: АОИ.01), предназначенной для проведения "
    "автоматической оптической инспекции печатных узлов оператором через "
    "веб-интерфейс с захватом изображения и просмотром результатов детекции."
)

PROG_TEXTS = [
    "Программный комплекс «АОИ-Web» предназначен для разработки, настройки и сопровождения "
    "программной части ПАК автоматической оптической инспекции печатных узлов.",
    "Разработка и модификация серверной логики (FastAPI), настройка моделей YOLOv8, "
    "конфигурирование БД и storage, интеграция WLED, администрирование пользователей.",
    "Для выполнения программы требуется рабочая станция с Python 3.10+ или portable-сборка "
    "AOI-Web-Portable-HTTPS.exe, доступ в локальную сеть.",
    "Рекомендуется не менее 8 Гбайт ОЗУ; для обучения моделей — 16 Гбайт и более.",
    "ПК x64, жёсткий диск (≥ 5 Гбайт), сетевой интерфейс; опционально GPU CUDA, смартфон для /phone, WLED.",
    "Сеть LAN; порт сервера 8000 (или AOI_WEB_PORT); WLED доступен по HTTP (/json/info, /json/state).",
    "Windows 10/11, Python 3.10+, requirements.txt; portable exe; браузер для проверки UI/API.",
    "Программист: Python, HTTP/REST, SQL, Git, основы CV/ML (YOLO), работа с .env и логами.",
    "Клиент–серверное приложение: SPA + FastAPI + SQLite/PostgreSQL + storage + YOLOv8 + WLED gateway.",
    "Непрерывный режим веб-сервера (Uvicorn/HTTPS). Обслуживание запросов операторов и API.",
    "Логирование logs/aoi.log, OpenAPI (/docs), pytest, smoke-тест portable-сборки.",
    "JWT-аутентификация, роли operator/manager/admin, hot-reload датасета, fallback-детектор.",
    "При сбое запроса сервер продолжает работу; данные сохраняются в БД/storage; перезапуск — вручную.",
    "Portable: AOI-Web-Portable-HTTPS.exe. Dev: uvicorn app.main:app --host 0.0.0.0 --port 8000.",
    "Настройка .env, SECRET_KEY, MODEL_WEIGHTS_PATH, PUBLIC_BASE_URL, параметры детекции и WLED в админ-панели.",
    "Управление датасетами (.pt), обучение scripts/train_unified.py, отладка WLED (discover/probe/debug).",
    "Сборка portable: scripts/build_portable_https.ps1 с сохранением aoi.db, storage, models.",
    "Остановка: Ctrl+C или закрытие окна portable; для dev — остановка uvicorn.",
    "Вход: изображения, JSON API, конфигурация .env, ответы WLED, веса моделей (.pt).",
    "Выход: протоколы инспекций, файлы storage, отчёты PDF/CSV, логи, JSON-ответы API.",
    "«Application startup complete.» — сервер запущен. Проверить https://localhost:8000/ и /docs.",
    "«Порт N недоступен...» / «Ошибка импорта app.main» — освободить порт, проверить _internal portable.",
]

OPER_TEXTS = [
    "«АОИ-Web» предназначена для автоматической оптической инспекции печатных узлов: "
    "захват изображения, детекция дефектов, просмотр результатов.",
    "Эксплуатация через браузер на рабочем месте оператора; доступ к серверу по HTTP/HTTPS в LAN.",
    "Вход в систему; создание инспекции; захват/загрузка изображения; просмотр результатов; экспорт протокола.",
    "Инспекция: QR/ссылка /phone или загрузка фото, запуск анализа, просмотр разметки дефектов.",
    "Журнал инспекций: просмотр своих записей, повторное открытие результата.",
    "ПК/планшет с браузером; смартфон с камерой (сценарий /phone); LAN до сервера АОИ-Web.",
    "Chrome/Edge/Firefox; на телефоне — браузер с доступом к камере.",
    "Оператор должен уметь работать с веб-интерфейсом, выполнять съёмку платы и читать результаты детекции.",
    "Открыть адрес сервера (например https://192.168.x.x:8000/), войти под учётной записью оператора.",
    "Вкладка «Инспекция»: новая инспекция, параметры платы, получение ссылки для телефона или загрузка файла.",
    "Дождаться анализа; просмотреть дефекты на снимке; при необходимости — оценка/проверка результата.",
    "Экспорт протокола (CSV/PDF) при наличии прав; сохранение записи в журнале.",
    "Выход из системы (кнопка «Выход») или закрытие вкладки браузера.",
    "«Неверный логин или пароль» — повторить вход; при блокировке обратиться к администратору.",
    "«Ошибка загрузки изображения» / «Камера недоступна» — разрешения браузера, Wi‑Fi, повторить съёмку.",
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


def apply_common(doc, doc_code: str, lu_code: str) -> None:
    replace_all(doc, "ПРОГРАММА ОЧИСТКИ ОПЕРАТИВНОЙ ПАМЯТИ", TITLE)
    replace_all(doc, "А.В.00001-01 33 01-ЛУ", lu_code)
    replace_all(doc, "А.В.00001-01 34 01-ЛУ", lu_code)
    replace_all(doc, "А.В.00001-01 33 01", doc_code)
    replace_all(doc, "А.В.00001-01 34 01", doc_code)
    # defensive replacements for legacy template text variants
    replace_all(doc, "Mem.ехе", "АОИ-Web")
    replace_all(doc, "Mem.exe", "АОИ-Web")
    replace_all(doc, "«Mem.ехе»", "«АОИ-Web»")
    replace_all(doc, "«Mem.exe»", "«АОИ-Web»")
    replace_all(
        doc,
        "очистки и дефрагментации оперативной памяти ПК через заданные интервалы времени",
        "автоматической оптической инспекции печатных узлов через web-интерфейс",
    )


def fill_programmer(doc) -> None:
    apply_common(doc, "АОИ.01.33 01", "АОИ.01.33 01-ЛУ")
    replace_all(doc, PROG_ANNO_OLD, PROG_ANNO_NEW)
    replace_all(doc, OPER_ANNO_OLD, PROG_ANNO_NEW)
    for t in PROG_TEXTS[:8]:
        replace_first(doc, "Текст", t)
    replace_first(doc, "текст", PROG_TEXTS[8])
    for t in PROG_TEXTS[9:]:
        replace_first(doc, "Текст", t)


def fill_operator(doc) -> None:
    apply_common(doc, "АОИ.01.34 01", "АОИ.01.34 01-ЛУ")
    replace_all(doc, OPER_ANNO_OLD, OPER_ANNO_NEW)
    replace_all(doc, PROG_ANNO_OLD, OPER_ANNO_NEW)
    for t in OPER_TEXTS:
        replace_first(doc, "Текст", t)


def process_template(template: Path, out: Path, filler) -> None:
    if not template.exists():
        raise FileNotFoundError(template)

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = None
    try:
        doc = word.Documents.Open(str(template), ReadOnly=False, Visible=False)
        filler(doc)
        doc.SaveAs2(str(out), FileFormat=16)
        doc.Close(False)
        print(f"Saved: {out}")
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        word.Quit()


def main() -> int:
    process_template(PROG_TEMPLATE, PROG_OUT, fill_programmer)
    process_template(OPER_TEMPLATE, OPER_OUT, fill_operator)
    return 0


if __name__ == "__main__":
    sys.exit(main())
