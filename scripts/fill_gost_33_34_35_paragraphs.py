"""Fill 33/34/35 GOST templates by replacing paragraph placeholders.

This approach avoids fragile Find/Range replacement issues in old .dot templates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import win32com.client  # type: ignore


DESK = Path(r"c:\Users\Neizy\Desktop")
TPL33 = DESK / "33 Руководство программиста.dot"
TPL34 = DESK / "34 Руководство оператора.dot"
OUT33 = DESK / "АОИ.01.33 01 — Руководство программиста (АОИ-Web).docx"
OUT34 = DESK / "АОИ.01.34 01 — Руководство оператора (АОИ-Web).docx"
OUT35 = DESK / "АОИ.01.35 01 — Руководство руководителя (АОИ-Web).docx"

TITLE = "АОИ-WEB — ПРОГРАММНАЯ ЧАСТЬ ПАК АВТОМАТИЧЕСКОЙ ОПТИЧЕСКОЙ ИНСПЕКЦИИ"

PROG_PARAS = [
    "Программный комплекс «АОИ-Web» предназначен для разработки, настройки и сопровождения программной части ПАК автоматической оптической инспекции печатных узлов.",
    "Разработка и модификация серверной логики (FastAPI), настройка моделей YOLOv8, конфигурирование БД и storage, интеграция WLED, администрирование пользователей.",
    "Для выполнения программы требуется рабочая станция с Python 3.10+ или portable-сборка AOI-Web-Portable-HTTPS.exe, доступ в локальную сеть.",
    "Рекомендуется не менее 8 Гбайт ОЗУ; для обучения моделей — 16 Гбайт и более.",
    "ПК x64, диск ≥ 5 Гбайт, сетевой интерфейс; опционально GPU CUDA, смартфон для /phone, контроллер WLED.",
    "Сеть LAN; порт сервера 8000 (или AOI_WEB_PORT); WLED доступен по HTTP (/json/info, /json/state).",
    "Windows 10/11, Python 3.10+, зависимости requirements.txt, браузер для проверки UI/API.",
    "Программист: Python, HTTP/REST, SQL, Git, основы CV/ML (YOLO), работа с .env и логами.",
    "Клиент-серверная архитектура: SPA + FastAPI + SQLite/PostgreSQL + storage + YOLOv8 + WLED gateway.",
    "Непрерывный режим веб-сервера (Uvicorn/HTTPS), обработка запросов операторов/руководителей/админов.",
    "Контроль: logs/aoi.log, OpenAPI (/docs), pytest, smoke-тест portable-сборки.",
    "JWT-аутентификация, роли operator/manager/admin, hot-reload датасета, fallback-детектор.",
    "При сбое отдельного запроса сервер продолжает работу; при аварийном завершении требуется перезапуск.",
    "Запуск: AOI-Web-Portable-HTTPS.exe либо uvicorn app.main:app --host 0.0.0.0 --port 8000.",
    "Настройка .env: SECRET_KEY, MODEL_WEIGHTS_PATH, PUBLIC_BASE_URL, параметры детекции и WLED.",
    "Управление датасетами (.pt), обучение scripts/train_unified.py, отладка WLED (discover/probe/debug).",
    "Сборка portable: scripts/build_portable_https.ps1 с переносом aoi.db, storage, models.",
    "Остановка: Ctrl+C или закрытие окна portable; для dev — остановка uvicorn.",
    "Вход: изображения, JSON API, конфигурация .env, ответы WLED, веса моделей (.pt).",
    "Выход: протоколы инспекций, файлы storage, отчёты PDF/CSV, логи, JSON-ответы API.",
    "«Application startup complete.» — сервер запущен успешно.",
    "«Порт недоступен»/«Ошибка импорта app.main» — проверить порт, конфиг и целостность поставки.",
]

OPER_PARAS = [
    "«АОИ-Web» предназначена для автоматической оптической инспекции печатных узлов: захват изображения, детекция дефектов, просмотр результатов.",
    "Эксплуатация через браузер на рабочем месте оператора; доступ к серверу по HTTP/HTTPS в LAN.",
    "Вход в систему; создание инспекции; захват/загрузка изображения; просмотр результатов; экспорт протокола.",
    "Инспекция: QR/ссылка /phone или загрузка фото, запуск анализа, просмотр разметки дефектов.",
    "Журнал инспекций: просмотр своих записей, повторное открытие результата.",
    "ПК/планшет с браузером; смартфон с камерой (сценарий /phone); LAN до сервера АОИ-Web.",
    "Chrome/Edge/Firefox; на телефоне — браузер с доступом к камере.",
    "Оператор должен уметь работать с веб-интерфейсом, выполнять съёмку платы и читать результаты детекции.",
    "Открыть адрес сервера (например https://192.168.x.x:8000/), войти под учётной записью оператора.",
    "Вкладка «Инспекция»: новая инспекция, параметры платы, ссылка для телефона или загрузка файла.",
    "Дождаться анализа; просмотреть дефекты на снимке; при необходимости — оценка/проверка результата.",
    "Экспорт протокола (CSV/PDF) при наличии прав; сохранение записи в журнале.",
    "Выход из системы (кнопка «Выход») или закрытие вкладки браузера.",
    "«Неверный логин или пароль» — повторить вход; при блокировке обратиться к администратору.",
    "«Ошибка загрузки изображения» / «Камера недоступна» — разрешения браузера, Wi‑Fi, повторить съёмку.",
]

MGR_PARAS = [
    "«АОИ-Web» предназначена для автоматической оптической инспекции печатных узлов: руководитель контролирует общий процесс и результаты.",
    "Руководитель работает со сводным журналом и статистикой, контролирует датасеты и устройства в рамках прав manager.",
    "Функции: общий журнал, статистика, датасеты, устройства, пользовательский список (просмотр).",
    "Функция: анализ метрик по дефектам и динамике в разделе «Статистика».",
    "Функция: контроль состояния устройств и активного датасета.",
    "Условия: браузер, LAN доступ к серверу, роль manager.",
    "Браузер Chrome/Edge/Firefox.",
    "Руководитель интерпретирует отчеты, контролирует качество и назначает повторные проверки.",
    "Войти по учётной записи руководителя и открыть разделы «Журнал», «Статистика», «Датасеты», «Устройства».",
    "Использовать фильтры, анализировать протоколы и тренды дефектов.",
    "Формировать замечания операторам и задачи на повторные проверки.",
    "Экспортировать сводные отчеты при необходимости.",
    "Завершить сессию и выйти из системы.",
    "«Недостаточно прав» — обратиться к администратору для admin-операций.",
    "«Ошибка загрузки данных» — проверить доступность сервера и повторить операцию.",
]


def replace_common(doc, code: str, lu: str, role_title: str | None = None) -> None:
    rng = doc.Content
    rng.Find.Execute("ПРОГРАММА ОЧИСТКИ ОПЕРАТИВНОЙ ПАМЯТИ", False, False, False, False, False, True, 1, False, TITLE, 2)
    rng.Find.Execute("А.В.00001-01 33 01-ЛУ", False, False, False, False, False, True, 1, False, lu, 2)
    rng.Find.Execute("А.В.00001-01 34 01-ЛУ", False, False, False, False, False, True, 1, False, lu, 2)
    rng.Find.Execute("А.В.00001-01 33 01", False, False, False, False, False, True, 1, False, code, 2)
    rng.Find.Execute("А.В.00001-01 34 01", False, False, False, False, False, True, 1, False, code, 2)
    rng.Find.Execute("Mem.ехе", False, False, False, False, False, True, 1, False, "АОИ-Web", 2)
    rng.Find.Execute("Mem.exe", False, False, False, False, False, True, 1, False, "АОИ-Web", 2)
    rng.Find.Execute("FreeMemory", False, False, False, False, False, True, 1, False, "АОИ-Web", 2)
    if role_title:
        rng.Find.Execute("Руководство оператора", False, False, False, False, False, True, 1, False, role_title, 2)


def fill_paragraph_placeholders(doc, texts: list[str]) -> None:
    i = 0
    for p in doc.Paragraphs:
        txt = p.Range.Text.strip()
        if txt in ("Текст", "текст") and i < len(texts):
            p.Range.Text = texts[i] + "\r"
            i += 1
    if i < len(texts):
        raise RuntimeError(f"Not enough placeholder paragraphs; filled={i}, expected={len(texts)}")


def patch_annotation(doc, new_sentence: str) -> None:
    # Replace the explicit old phrase wherever it appears in annotation.
    old = "предназначенной для очистки и дефрагментации оперативной памяти ПК через заданные интервалы времени."
    for p in doc.Paragraphs:
        if old in p.Range.Text:
            p.Range.Text = p.Range.Text.replace(old, new_sentence)
            break


def process(template: Path, out: Path, code: str, lu: str, texts: list[str], role_title: str | None, anno: str) -> None:
    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = None
    try:
        doc = word.Documents.Open(str(template), ReadOnly=False, Visible=False)
        replace_common(doc, code, lu, role_title)
        patch_annotation(doc, anno)
        fill_paragraph_placeholders(doc, texts)
        doc.SaveAs2(str(out), FileFormat=16)
        print(f"Saved: {out}")
        doc.Close(False)
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        word.Quit()


def main() -> int:
    process(
        TPL33,
        OUT33,
        code="АОИ.01.33 01",
        lu="АОИ.01.33 01-ЛУ",
        texts=PROG_PARAS,
        role_title=None,
        anno="предназначенной для автоматической оптической инспекции печатных узлов.",
    )
    process(
        TPL34,
        OUT34,
        code="АОИ.01.34 01",
        lu="АОИ.01.34 01-ЛУ",
        texts=OPER_PARAS,
        role_title="Руководство оператора",
        anno="предназначенной для автоматической оптической инспекции печатных узлов оператором.",
    )
    process(
        TPL34,
        OUT35,
        code="АОИ.01.35 01",
        lu="АОИ.01.35 01-ЛУ",
        texts=MGR_PARAS,
        role_title="Руководство руководителя",
        anno="предназначенной для автоматической оптической инспекции печатных узлов руководителем.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

