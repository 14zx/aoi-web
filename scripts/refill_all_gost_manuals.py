"""Re-fill all GOST manuals on Desktop with AOI-Web content (robust Word COM)."""

from __future__ import annotations

import sys
from pathlib import Path

import win32com.client  # type: ignore

DESK = Path(r"c:\Users\Neizy\Desktop")

TITLE = "АОИ-WEB — ПРОГРАММНАЯ ЧАСТЬ ПАК АВТОМАТИЧЕСКОЙ ОПТИЧЕСКОЙ ИНСПЕКЦИИ"

WD_REPLACE_ALL = 2
WD_REPLACE_ONE = 1

# wdHeaderFooterPrimary=1, FirstPage=2, EvenPages=3
HF_TYPES = (1, 2, 3)


def _find_replace(
    range_obj,
    find: str,
    repl: str,
    replace_all: bool = True,
    wildcards: bool = False,
) -> bool:
    rng = range_obj
    rng.Find.ClearFormatting()
    rng.Find.Replacement.ClearFormatting()
    return bool(
        rng.Find.Execute(
            FindText=find,
            MatchCase=False,
            MatchWholeWord=False,
            MatchWildcards=wildcards,
            Forward=True,
            Wrap=1,
            Format=False,
            ReplaceWith=repl,
            Replace=WD_REPLACE_ALL if replace_all else WD_REPLACE_ONE,
        )
    )


def replace_wildcards(doc, find: str, repl: str) -> None:
    _find_replace(doc.Content, find, repl, replace_all=True, wildcards=True)
    story = doc.StoryRanges(1)
    while story is not None:
        try:
            _find_replace(story, find, repl, replace_all=True, wildcards=True)
        except Exception:
            pass
        try:
            story = story.NextStoryRange
        except Exception:
            story = None
    for shape in doc.Shapes:
        try:
            if shape.TextFrame.HasText:
                _find_replace(shape.TextFrame.TextRange, find, repl, replace_all=True, wildcards=True)
        except Exception:
            pass


def replace_everywhere(doc, find: str, repl: str) -> None:
    _find_replace(doc.Content, find, repl, replace_all=True)

    story = doc.StoryRanges(1)
    while story is not None:
        try:
            _find_replace(story, find, repl, replace_all=True)
        except Exception:
            pass
        try:
            nxt = story.NextStoryRange
            story = nxt if nxt is not None else None
        except Exception:
            story = None

    for shape in doc.Shapes:
        try:
            if shape.TextFrame.HasText:
                _find_replace(shape.TextFrame.TextRange, find, repl, replace_all=True)
        except Exception:
            pass

    for ti in range(1, doc.Tables.Count + 1):
        try:
            table = doc.Tables(ti)
            for ri in range(1, table.Rows.Count + 1):
                try:
                    row = table.Rows(ri)
                    for ci in range(1, row.Cells.Count + 1):
                        try:
                            _find_replace(row.Cells(ci).Range, find, repl, replace_all=True)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

    for si in range(1, doc.Sections.Count + 1):
        sec = doc.Sections(si)
        for hf_kind in HF_TYPES:
            try:
                hdr = sec.Headers(hf_kind)
                if hdr.Exists:
                    _find_replace(hdr.Range, find, repl, replace_all=True)
            except Exception:
                pass
            try:
                ftr = sec.Footers(hf_kind)
                if ftr.Exists:
                    _find_replace(ftr.Range, find, repl, replace_all=True)
            except Exception:
                pass


def replace_placeholders(doc, texts: list[str]) -> int:
    """Replace paragraphs/cells that are exactly 'Текст' or 'текст'."""
    idx = 0

    def try_fill(rng) -> bool:
        nonlocal idx
        if idx >= len(texts):
            return False
        raw = rng.Text.replace("\x07", "").replace("\r", "").strip()
        if raw not in ("Текст", "текст"):
            return False
        rng.Text = texts[idx] + "\r"
        idx += 1
        return True

    for para in doc.Paragraphs:
        try_fill(para.Range)

    for ti in range(1, doc.Tables.Count + 1):
        try:
            table = doc.Tables(ti)
            for ri in range(1, table.Rows.Count + 1):
                try:
                    row = table.Rows(ri)
                    for ci in range(1, row.Cells.Count + 1):
                        try_fill(row.Cells(ci).Range)
                except Exception:
                    pass
        except Exception:
            pass
    return idx


def replace_annotation_mem(doc, new_text: str) -> None:
    patterns = [
        "[[[«Mem.ехе», предназначенной для очистки\r"
        "и дефрагментации оперативной памяти ПК через заданные интервалы времени.]]]",
        "[[[«Mem.ехе», предназначенной для\r\n"
        "очистки и дефрагментации оперативной памяти ПК через заданные интервалы\r\n"
        "времени.]]]",
        "[[[«Mem.exe», предназначенной для очистки\r"
        "и дефрагментации оперативной памяти ПК через заданные интервалы времени.]]]",
    ]
    for p in patterns:
        replace_everywhere(doc, p, new_text)
    # Блок аннотации: без [ ] в шаблоне подстановки (Word трактует их как класс символов)
    replace_wildcards(doc, "«Mem*времени.»»»", new_text)
    replace_wildcards(doc, "«Mem*»»»", new_text)
    replace_everywhere(doc, "«Mem.ехе»", "«АОИ-Web»")
    replace_everywhere(doc, "«Mem.exe»", "«АОИ-Web»")
    replace_everywhere(doc, "Mem.ехе", "АОИ-Web")
    replace_everywhere(doc, "Mem.exe", "АОИ-Web")
    replace_everywhere(
        doc,
        "предназначенной для очистки и дефрагментации оперативной памяти ПК",
        "предназначенной для автоматической оптической инспекции печатных узлов",
    )
    replace_wildcards(
        doc,
        "предназначенной для*оперативной памяти ПК*",
        "предназначенной для автоматической оптической инспекции печатных узлов",
    )


def apply_codes(doc, doc_code: str, lu_code: str) -> None:
    replace_everywhere(doc, "ПРОГРАММА ОЧИСТКИ ОПЕРАТИВНОЙ ПАМЯТИ", TITLE)
    replace_everywhere(doc, "ПРОГРАММА ОЧИСТКИ\rОПЕРАТИВНОЙ ПАМЯТИ", TITLE)
    for old in (
        "А.В.00001-01 32 01-ЛУ",
        "А.В.00001-01 33 01-ЛУ",
        "А.В.00001-01 34 01-ЛУ",
        "А.В.00001-01 35 01-ЛУ",
        "А.В.00001-01 32 01",
        "А.В.00001-01 33 01",
        "А.В.00001-01 34 01",
        "А.В.00001-01 35 01",
    ):
        if old.endswith("-ЛУ"):
            replace_everywhere(doc, old, lu_code)
        else:
            replace_everywhere(doc, old, doc_code)
    # Титульный лист: коды и заголовок иногда в рамках/полях с разрывами строк
    replace_wildcards(doc, "А.В.00001-01 * 01-ЛУ", lu_code)
    replace_wildcards(doc, "А.В.00001-01 * 01", doc_code)
    replace_wildcards(doc, "ПРОГРАММА ОЧИСТКИ*ПАМЯТИ", TITLE)


SYS_PROG_TEXTS = [
    "Программный комплекс «АОИ-Web» предназначен для автоматизации оптического контроля печатных узлов "
    "в составе ПАК. Системный программист обеспечивает установку, настройку и сопровождение сервера, БД, моделей и сети.",
    "Установка и обновление ПО; настройка .env; развёртывание portable или uvicorn; интеграция WLED; резервное копирование aoi.db и storage.",
    "Рабочая станция x64, ОЗУ ≥ 8 Гбайт, диск ≥ 5 Гбайт, сеть LAN; опционально GPU для обучения YOLO.",
    "Рекомендуется не менее 8 Гбайт ОЗУ (16 Гбайт при обучении моделей).",
    "ПК, сетевой интерфейс, при необходимости смартфон для проверки /phone, контроллер WLED.",
    "Порт 8000 (или AOI_WEB_PORT), доступность WLED по HTTP, корректный PUBLIC_BASE_URL для телефона.",
    "Windows 10/11; Python 3.10+ и requirements.txt; либо AOI-Web-Portable-HTTPS.exe.",
    "Опыт администрирования Windows, Python, HTTP, SQL, чтение логов, базовые знания ML/CV.",
    "Клиент–сервер: FastAPI, SPA, SQLite/PostgreSQL, storage, YOLOv8, шлюз WLED.",
    "Сервер работает непрерывно (Uvicorn/HTTPS), обслуживает запросы операторов и API.",
    "Связи: API → сервисы → БД/storage; детектор синхронизируется с активным датасетом; WLED — HTTP JSON.",
    "Взаимодействие с браузерами, WLED (/json/info, /json/state), ОС (порты, firewall).",
    "Обеспечить LAN-доступ, PUBLIC_BASE_URL, доступность WLED по IP.",
    "Portable: запуск exe; dev: .venv, init_db, uvicorn. Переменные AOI_WEB_PORT, PUBLIC_BASE_URL.",
    "Проверка: / и /docs; smoke portable; тест WLED; pytest при изменениях кода.",
    "Остановка: Ctrl+C или закрытие окна exe; освобождение порта перед пересборкой portable.",
    "«Application startup complete.» — успешный запуск.",
    "«Порт N недоступен» — сменить AOI_WEB_PORT или включить AOI_WEB_PORT_FALLBACK=1.",
    "«Ошибка импорта app.main» — проверить целостность _internal и зависимости.",
    "Ошибки HTTP к WLED — проверить IP, сеть, firewall.",
    "Резервное копирование: каталоги _internal (aoi.db, storage, models, logs) перед обновлением portable.",
    "Журнал logs/aoi.log — основной источник диагностики при сопровождении.",
]

PROG_TEXTS = [
    "Программный комплекс «АОИ-Web» предназначен для разработки и сопровождения программной части ПАК АОИ.",
    "Разработка FastAPI-маршрутов и сервисов; настройка YOLOv8; конфигурация БД, storage, WLED; администрирование.",
    "Python 3.10+ или portable-сборка; доступ в локальную сеть.",
    "ОЗУ ≥ 8 Гбайт (рекомендуется).",
    "ПК x64, диск ≥ 5 Гбайт, LAN, опционально GPU, смартфон, WLED.",
    "Порт сервера, HTTP-доступ к WLED.",
    "Windows 10/11, Python, браузер; WLED при наличии подсветки.",
    "Python, HTTP/REST, SQL, Git, основы YOLO.",
    "Клиент–сервер: SPA + FastAPI + БД + YOLOv8.",
    "Непрерывный режим веб-сервера.",
    "Логи, /docs, pytest, smoke portable.",
    "JWT, роли, hot-reload датасета.",
    "При сбое запроса сервер продолжает работу; перезапуск вручную.",
    "Запуск portable exe или uvicorn.",
    "Настройка .env и админ-панели (WLED, датасеты).",
    "Обучение/дообучение scripts/train_unified.py.",
    "Сборка scripts/build_portable_https.ps1 с миграцией данных.",
    "Остановка Ctrl+C / закрытие окна.",
    "Вход: изображения, API, .env, WLED, веса .pt.",
    "Выход: протоколы, storage, отчёты, логи.",
    "Application startup complete — ОК.",
    "Порт занят / ошибка импорта — см. логи.",
]

OPER_TEXTS = [
    "«АОИ-Web» — автоматическая оптическая инспекция печатных узлов через веб-интерфейс.",
    "Работа оператора в браузере: инспекция, просмотр результатов.",
    "Вход; инспекция; загрузка/съёмка; просмотр результатов; экспорт.",
    "Съёмка через /phone или загрузка файла; анализ; просмотр дефектов.",
    "Журнал своих инспекций.",
    "ПК/смартфон, LAN до сервера.",
    "Браузер с поддержкой камеры на телефоне.",
    "Умение работать с веб-интерфейсом и камерой.",
    "Открыть https://<IP>:8000/, войти как оператор.",
    "Создать инспекцию, получить ссылку /phone или загрузить фото.",
    "Дождаться анализа, просмотреть дефекты.",
    "Экспорт протокола при наличии прав.",
    "Выход из системы.",
    "Неверный логин — повторить вход.",
    "Ошибка камеры/загрузки — проверить Wi‑Fi и разрешения.",
]

MANAGER_TEXTS = [
    "«АОИ-Web» — контроль процесса инспекции и сводных результатов руководителем.",
    "Просмотр журнала, статистики, датасетов и устройств (в пределах прав).",
    "Статистика; датасеты; устройства; пользователи (просмотр).",
    "Раздел «Статистика» — метрики по дефектам и динамика.",
    "Разделы «Датасеты» и «Устройства» — активная модель и регистрация устройств.",
    "Общий журнал инспекций всех операторов.",
    "ПК с браузером, LAN.",
    "Chrome/Edge/Firefox.",
    "Понимание сводных отчётов и настроек датасетов.",
    "Вход под учётной записью руководителя (manager).",
    "Работа на вкладках Статистика, Датасеты, Устройства.",
    "Просмотр протоколов, организация повторных проверок.",
    "Экспорт отчётов при наличии прав.",
    "Выход из системы.",
    "Недостаточно прав — обратиться к администратору.",
    "Ошибка загрузки данных — проверить сервер и повторить.",
]

ANNO = {
    "sys": "«АОИ-Web» (АОИ.01) — настройка и сопровождение программной части ПАК автоматической оптической инспекции.",
    "prog": "«АОИ-Web» (АОИ.01) — разработка и сопровождение программного комплекса автоматической оптической инспекции.",
    "oper": "«АОИ-Web» (АОИ.01) — эксплуатация оператором через веб-интерфейс с захватом изображения и просмотром результатов.",
    "mgr": "«АОИ-Web» (АОИ.01) — контроль процесса инспекции руководителем, статистика и управление датасетами.",
}


def process(
    template: Path,
    output: Path,
    doc_code: str,
    lu_code: str,
    texts: list[str],
    anno: str,
    extra: dict[str, str] | None = None,
) -> None:
    if not template.exists():
        raise FileNotFoundError(template)

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = None
    try:
        doc = word.Documents.Open(str(template), ReadOnly=False, Visible=False)
        try:
            if doc.ProtectionType != -1:
                doc.Unprotect()
        except Exception:
            pass

        apply_codes(doc, doc_code, lu_code)
        replace_annotation_mem(doc, anno)
        if extra:
            for k, v in extra.items():
                replace_everywhere(doc, k, v)

        n = replace_placeholders(doc, texts)
        expected = len(texts)
        if n < expected:
            print(f"  WARN {output.name}: replaced {n}/{expected} placeholders")

        doc.SaveAs2(str(output), FileFormat=16)
        print(f"OK: {output}")
        doc.Close(False)
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        word.Quit()


def _find_desktop(glob_pat: str) -> Path:
    matches = sorted(DESK.glob(glob_pat))
    if not matches:
        raise FileNotFoundError(f"No match on Desktop: {glob_pat}")
    return matches[0]


def main() -> int:
    # GOST .dot with «Текст» placeholders (same family as 33/34)
    tpl_gost = _find_desktop("33 Руководство программиста.dot")
    if not tpl_gost.exists():
        tpl_gost = sorted(DESK.glob("33*.dot"))[0]

    tpl33 = tpl_gost
    tpl34_all = sorted(DESK.glob("34*.dot"))
    if not tpl34_all:
        raise FileNotFoundError("34*.dot not found on Desktop")
    tpl34 = min(tpl34_all, key=lambda p: p.stat().st_size)
    tpl34_mgr = max(tpl34_all, key=lambda p: p.stat().st_size) if len(tpl34_all) > 1 else tpl34

    jobs = [
        (
            tpl33,
            DESK / "АОИ.01.32 01 — Руководство системного программиста (АОИ-Web).docx",
            "АОИ.01.32 01",
            "АОИ.01.32 01-ЛУ",
            SYS_PROG_TEXTS,
            ANNO["sys"],
            {
                "Руководство программиста": "Руководство системного программиста",
                "руководство программиста": "руководство системного программиста",
            },
        ),
        (
            tpl33,
            DESK / "АОИ.01.33 01 — Руководство программиста (АОИ-Web).docx",
            "АОИ.01.33 01",
            "АОИ.01.33 01-ЛУ",
            PROG_TEXTS,
            ANNO["prog"],
            None,
        ),
        (
            tpl34,
            DESK / "АОИ.01.34 01 — Руководство оператора (АОИ-Web).docx",
            "АОИ.01.34 01",
            "АОИ.01.34 01-ЛУ",
            OPER_TEXTS,
            ANNO["oper"],
            None,
        ),
        (
            tpl34_mgr,
            DESK / "АОИ.01.35 01 — Руководство руководителя (АОИ-Web).docx",
            "АОИ.01.35 01",
            "АОИ.01.35 01-ЛУ",
            MANAGER_TEXTS,
            ANNO["mgr"],
            {
                "Руководство оператора": "Руководство руководителя",
                "руководство оператора": "руководство руководителя",
            },
        ),
    ]

    for tpl, out, code, lu, texts, anno, extra in jobs:
        process(tpl, out, code, lu, texts, anno, extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
