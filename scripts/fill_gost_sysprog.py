"""Fill 32 manual (system programmer) in original styled .doc template."""

from __future__ import annotations

import sys
from pathlib import Path

import win32com.client  # type: ignore


DESK = Path(r"c:\Users\Neizy\Desktop")
OUT = DESK / "АОИ.01.32 01 — Руководство системного программиста (АОИ-Web).docx"


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


def replace_between(doc, start: str, end: str, body: str) -> None:
    content = doc.Content
    f = content.Duplicate
    f.Find.ClearFormatting()
    if not f.Find.Execute(start):
        raise RuntimeError(f"Start heading not found: {start!r}")
    start_pos = f.Start

    g = doc.Range(start_pos, doc.Content.End)
    g.Find.ClearFormatting()
    if not g.Find.Execute(end):
        raise RuntimeError(f"End heading not found: {end!r}")
    end_pos = g.Start

    target = doc.Range(start_pos, end_pos)
    target.Text = start + "\r" + body.strip() + "\r\r" + end


def source_32_doc() -> Path:
    cands = sorted(DESK.glob("00001-01 32 01*.doc"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise FileNotFoundError("Source 32 .doc template not found on Desktop")
    return cands[0]


def main() -> int:
    src = source_32_doc()

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = None
    try:
        doc = word.Documents.Open(str(src), ReadOnly=False, Visible=False)

        # title / code replacements
        replace_all(doc, "ПРОГРАММА ОЧИСТКИ ОПЕРАТИВНОЙ ПАМЯТИ", "АОИ-WEB — ПРОГРАММНАЯ ЧАСТЬ ПАК АВТОМАТИЧЕСКОЙ ОПТИЧЕСКОЙ ИНСПЕКЦИИ")
        replace_all(doc, "А.В.00001-01 32 01-ЛУ", "АОИ.01.32 01-ЛУ")
        replace_all(doc, "А.В.00001-01 32 01", "АОИ.01.32 01")
        replace_all(doc, "Mem.ехе", "АОИ-Web")
        replace_all(doc, "Mem.exe", "АОИ-Web")
        replace_all(doc, "FreeMemory", "АОИ-Web")

        replace_between(
            doc,
            "АННОТАЦИЯ",
            "СОДЕРЖАНИЕ",
            (
                "В данном программном документе приведено руководство системного программиста по настройке "
                "и использованию программного комплекса «АОИ-Web» (обозначение: АОИ.01), предназначенного "
                "для автоматической оптической инспекции печатных узлов.\r\n"
                "Комплекс обеспечивает захват изображения через веб-интерфейс (в том числе с камеры "
                "мобильного устройства), предварительную обработку кадра, детекцию объектов и дефектов с "
                "применением нейросетевой модели YOLOv8, опциональное выравнивание по эталону (Golden Board), "
                "формирование протоколов инспекции и ведение журнала.\r\n"
                "В разделах документа приведены сведения о назначении, структуре, настройке, проверке "
                "работоспособности и сообщениях системному программисту."
            ),
        )

        replace_between(
            doc,
            "1.1. Назначение программы",
            "1.2. Функции программы",
            (
                "Программный комплекс «АОИ-Web» предназначен для автоматической оптической инспекции "
                "печатных узлов в составе ПАК АОИ. Комплекс поддерживает web-доступ пользователей, "
                "анализ изображений плат, хранение результатов и отчетность."
            ),
        )
        replace_between(
            doc,
            "1.2. Функции программы",
            "1.3. Минимальный состав технических средств",
            (
                "Основные функции комплекса:\r\n"
                "• аутентификация пользователей и разграничение прав (operator/manager/admin);\r\n"
                "• создание и выполнение инспекций с детекцией дефектов (YOLOv8);\r\n"
                "• ведение журнала инспекций и экспорт протоколов;\r\n"
                "• управление датасетами/весами модели;\r\n"
                "• опциональное выравнивание по Golden Board;\r\n"
                "• управление подсветкой через WLED (JSON API)."
            ),
        )
        replace_between(
            doc,
            "1.3. Минимальный состав технических средств",
            "1.4. Минимальный состав программных средств",
            (
                "Минимальный состав технических средств:\r\n"
                "• ПК x64;\r\n"
                "• ОЗУ не менее 8 Гбайт (рекомендуется 16 Гбайт для обучения/тяжёлых моделей);\r\n"
                "• свободное дисковое пространство не менее 5 Гбайт;\r\n"
                "• сетевой доступ LAN;\r\n"
                "• устройство захвата изображения (смартфон с камерой или иной источник фото)."
            ),
        )
        replace_between(
            doc,
            "1.4. Минимальный состав программных средств",
            "1.5. Требования к персоналу",
            (
                "Программные средства:\r\n"
                "• ОС Windows 10/11;\r\n"
                "• для запуска из исходников — Python 3.10+ и зависимости requirements.txt;\r\n"
                "• для эксплуатации — современный браузер (Chrome/Edge/Firefox);\r\n"
                "• для подсветки — контроллер WLED, доступный в локальной сети."
            ),
        )
        replace_between(
            doc,
            "1.5. Требования к персоналу (системному программисту)",
            "2. СТРУКТУРА ПРОГРАММЫ",
            (
                "Системный программист должен иметь техническую подготовку и навыки сопровождения "
                "ПО под Windows, настройки сети, работы с .env, анализа логов и устранения ошибок запуска."
            ),
        )

        replace_between(
            doc,
            "2.1. Сведения о структуре программы",
            "2.2. Сведения о составных частях программы",
            "Комплекс имеет клиент-серверную архитектуру: SPA-клиент + FastAPI backend + БД + storage + сервисы ML/CV.",
        )
        replace_between(
            doc,
            "2.2. Сведения о составных частях программы",
            "2.3. Сведения о связях между составными частями программы",
            (
                "Составные части:\r\n"
                "• app/main.py — точка входа;\r\n"
                "• app/api/* — маршруты API;\r\n"
                "• app/services/* — детекция, preprocessing, alignment, hardware gateway;\r\n"
                "• app/models/* и alembic/* — модели и миграции БД;\r\n"
                "• app/static/* — интерфейс пользователя."
            ),
        )
        replace_between(
            doc,
            "2.3. Сведения о связях между составными частями программы",
            "2.4. Сведения о связях с другими программами",
            "API вызывает сервисы, сервисы используют настройки, БД и хранилище; модуль подсветки взаимодействует с WLED по HTTP.",
        )
        replace_between(
            doc,
            "2.4. Сведения о связях с другими программами",
            "3. НАСТРОЙКА ПРОГРАММЫ",
            "Комплекс взаимодействует с веб-браузерами, системой хранения данных, а также с устройством WLED через JSON API.",
        )

        replace_between(
            doc,
            "3.1. Настройка на состав технических средств",
            "3.2. Настройка на состав программных средств",
            (
                "Проверить доступность сервера в локальной сети, корректность PUBLIC_BASE_URL для телефона, "
                "наличие доступа к камере мобильного устройства и (при необходимости) доступность WLED по IP."
            ),
        )
        replace_between(
            doc,
            "3.2. Настройка на состав программных средств",
            "4. ПРОВЕРКА ПРОГРАММЫ",
            (
                "Настройка выполняется через .env и/или админ-интерфейс:\r\n"
                "• SECRET_KEY, DATABASE_URL, MODEL_WEIGHTS_PATH, PUBLIC_BASE_URL;\r\n"
                "• запуск dev: uvicorn app.main:app --host 0.0.0.0 --port 8000;\r\n"
                "• portable: AOI-Web-Portable-HTTPS.exe;\r\n"
                "• настройка WLED: base URL, /json/info, /json/state, segment/transition."
            ),
        )

        replace_between(
            doc,
            "4.1. Описание способов проверки",
            "4.2. Методы прогона",
            (
                "Работоспособность проверяется:\r\n"
                "1) доступностью / и /docs;\r\n"
                "2) выполнением тестовой инспекции;\r\n"
                "3) проверкой записи результатов в БД и storage;\r\n"
                "4) проверкой WLED probe/control (если используется)."
            ),
        )
        replace_between(
            doc,
            "4.2.1. Проверка работоспособности программы",
            "4.2.2. Проверка на сообщение об ошибке",
            (
                "1) Запустить сервер АОИ-Web.\r\n"
                "2) Авторизоваться и выполнить тестовую инспекцию изображения.\r\n"
                "3) Убедиться, что результат сохранен в журнале и отображается в интерфейсе.\r\n"
                "4) Проверить /api/health и /docs."
            ),
        )
        replace_between(
            doc,
            "4.2.2. Проверка на сообщение об ошибке",
            "5. СООБЩЕНИЯ СИСТЕМНОМУ ПРОГРАММИСТУ",
            (
                "1) Занять порт 8000 сторонним приложением и запустить комплекс.\r\n"
                "2) Убедиться в сообщении о недоступности порта.\r\n"
                "3) Указать неверный адрес WLED и выполнить probe — проверить диагностическое сообщение."
            ),
        )
        replace_between(
            doc,
            "5. СООБЩЕНИЯ СИСТЕМНОМУ ПРОГРАММИСТУ",
            "Лист регистрации изменений",
            (
                "Типовые сообщения и действия:\r\n"
                "• Application startup complete — запуск успешен.\r\n"
                "• Порт недоступен — освободить порт или задать AOI_WEB_PORT.\r\n"
                "• Ошибка импорта app.main — проверить целостность поставки/зависимости.\r\n"
                "• Ошибка связи с WLED — проверить IP/сеть/доступность контроллера."
            ),
        )

        doc.SaveAs2(str(OUT), FileFormat=16)
        print(f"Saved: {OUT}")
        doc.Close(False)
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        word.Quit()

    return 0


if __name__ == "__main__":
    sys.exit(main())

