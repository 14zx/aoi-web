# АОИ-Web — программная часть ПАК автоматической оптической инспекции печатных узлов

Обозначение: **АОИ.01**. Разработано в рамках дипломного проекта в соответствии с
техническим заданием **АОИ.01.ТЗ** (ГОСТ 19.201-78).

Программа предназначена для автоматического оптического контроля печатных узлов
(сборка, пайка, геометрия компонентов) с захватом изображения с камеры мобильного
устройства через веб-интерфейс, детекцией объектов свёрточной нейросетью
(Ultralytics YOLOv8), опциональным выравниванием кадра по эталону (Golden Board)
и формированием протокола инспекции.

## Классы объектов детектора (актуальный состав)

**Важно:** перечень классов, которые реально возвращает система, **не зашит в
документации одной таблицей**. Он определяется **активной моделью YOLO** и
настройками детектора в коде.

- Актуальный список кодов, подписей и цветов для интерфейса отдаётся в
  `GET /api/meta` (поле `defect_classes`) и собирается в
  `Detector.get_defect_classes()` (`app/services/detector.py`).
- Сырые имена классов из разных публичных весов нормализуются через таблицу
  `CLASS_ALIASES` в том же модуле; полностью неизвестные имена сохраняются как есть.
- В коде зафиксирован **базовый справочник из шести кодов** (`open`, `short`,
  `mousebite`, `spur`, `copper`, `pinhole`) — это **якорь совместимости** с
  распространёнными датасетами **дефектов изготовления платы / трассировки**
  (в духе DeepPCB и аналогов), а не «официальный перечень монтажных дефектов»
  по IPC-A-610. При подключении других весов набор меток меняется (см. пресеты
  в разделе «Модель детекции» ниже).
- При включённой опции и корректном `unified_classes.yaml` может подключаться
  расширенный реестр классов (см. `_load_unified_classes()` в `detector.py`).
- После инференса возможны **дополнительные логические коды** пайплайна
  (например, `placement_tilt` при нарушении ориентации относительно эталона) —
  см. `app/services/post_detection.py`.

То есть таблица «шесть дефектов трассировки» из старых версий README описывала
**типичный публичный датасет**, а не целевую номенклатуру монтажа ПАК целиком.

## Технологический стек (ТЗ п. 4.5)

- **Серверная часть:** Python 3.10+, FastAPI, Uvicorn, SQLAlchemy 2, Alembic, Pydantic v2.
- **Компьютерное зрение / ML:** PyTorch 2+, Ultralytics YOLOv8, OpenCV 4.8+, NumPy, Pillow.
- **Безопасность:** passlib[bcrypt], python-jose (JWT), HTTPS (через обратный прокси).
- **Отчёты:** ReportLab (PDF), стандартный csv (CSV).
- **СУБД:** PostgreSQL 14+ (prod) / SQLite 3.35+ (dev).
- **Клиентская часть:** HTML5, CSS3, JavaScript ES2020 (без внешних зависимостей).

## Структура проекта

```
diplome/
├── app/
│   ├── main.py              # Точка входа FastAPI
│   ├── config.py            # Настройки (pydantic-settings)
│   ├── database.py          # Подключение к БД, сессии
│   ├── models/              # ORM-модели (User, Inspection, Defect, AuditLog, LoginAttempt)
│   ├── schemas/             # Pydantic-схемы запросов/ответов
│   ├── api/                 # HTTP-маршруты (auth, users, inspections, stats)
│   ├── core/                # Безопасность (JWT/bcrypt), логирование
│   ├── services/            # Бизнес-логика: препроцессинг, детектор, визуализация, отчёты
│   └── static/              # Клиентская часть (HTML/CSS/JS)
├── alembic/                 # Миграции БД
├── scripts/                 # Утилиты (init_db, train)
├── tests/                   # pytest
├── storage/                 # Хранилище изображений (создаётся автоматически)
├── models/                  # Веса нейросетевой модели
├── logs/                    # Лог-файлы
├── requirements.txt
├── alembic.ini
└── README.md
```

## Установка и запуск

### Публикация на GitHub и portable-релиз

Инструкция: [docs/GITHUB_PUBLISH.md](docs/GITHUB_PUBLISH.md) (исходники в репозиторий, ZIP со сборкой — в **Releases**).

Кратко: `build_portable_https.bat portable_dist_release /SkipMigrate`, затем `scripts\package_release.ps1`.

### 1. Клонирование и установка зависимостей

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

> **PyTorch:** на серверном узле с GPU устанавливать вариант с CUDA согласно
> [официальной инструкции](https://pytorch.org/get-started/locally/). Например:
> `pip install torch==2.4.1+cu121 --index-url https://download.pytorch.org/whl/cu121`.

### 2. Конфигурация

```bash
cp .env.example .env            # Linux/macOS
copy .env.example .env          # Windows
```

Откройте `.env` и установите `SECRET_KEY` (не менее 32 случайных символов).

### 3. Инициализация базы данных и учётной записи администратора

```bash
python -m scripts.init_db
```

Скрипт создаст все таблицы и учётную запись администратора, параметры которой заданы
в `.env` (переменные `ADMIN_USERNAME`, `ADMIN_PASSWORD`).

### 4. Запуск сервера разработки

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Откройте в браузере:

- `http://localhost:8000/` — веб-интерфейс оператора / руководителя / администратора,
- `http://localhost:8000/docs` — автогенерируемая документация OpenAPI (Swagger),
- `http://localhost:8000/redoc` — альтернативная документация.

### 5. Продакшн-развёртывание (рекомендовано в ТЗ)

```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

Перед gunicorn настраивается обратный прокси (nginx) с SSL-сертификатом — как того
требует п. 4.5.4 ТЗ (обмен только по HTTPS).

## Веса моделей (отдельно от исходников в Git)

Файлы нейросети (`*.pt`, ~150 МБ и больше) **не входят** в основной репозиторий
GitHub — только код, конфиги и описание структуры.

| Что в Git | Что отдельно |
|-----------|----------------|
| `models/README.md`, `manifest.yaml`, `unified_classes.yaml` | все `*.pt`, архив весов |
| скрипты `download_pretrained`, `download_datasets` | Release **AOI-Web-models.zip** |

**Подробно:** [models/README.md](models/README.md)

Кратко:

1. **Архив с диплома / Release** — распаковать в корень проекта → каталог `models/`.
2. **Скачать из сети** — `python -m scripts.download_pretrained` и/или `download_datasets`.
3. **Проверка** — `python -m scripts.verify_models`.

Собрать ZIP для выкладки в GitHub Releases (на машине, где уже лежат веса):

```powershell
.\scripts\package_models_release.ps1 -Version 1.0.0
```

Файл `dist/AOI-Web-models-1.0.0.zip` прикрепите к релизу **вручную** (отдельно от portable-сборки).

## Модель детекции

### Вариант 1 (быстрый): готовые публичные веса

Приложение умеет работать с любыми YOLOv8-весами, обученными под дефекты PCB.
Чтобы сразу получить работающий детектор, используйте скрипт автозагрузки:

```bash
python -m scripts.download_datasets            # скачать все пресеты и активировать первый
python -m scripts.download_datasets --list     # посмотреть список пресетов
python -m scripts.download_datasets --only pku # скачать конкретный
```

Скрипт:
- скачивает веса из HuggingFace;
- сохраняет в `models/datasets/<id>/weights.pt`;
- создаёт запись в таблице `datasets`;
- делает первый успешно скачанный датасет активным (hot-reload детектора).

Доступные пресеты (см. `scripts/download_datasets.py`):

| Ключ | Источник | Классы |
|---|---|---|
| `pku` | [ampragatish/yolov8n-pcb-defects-detection](https://huggingface.co/ampragatish/yolov8n-pcb-defects-detection) | missing_hole, mouse_bite, open_circuit, short, spur, spurious_copper |
| `pcb_seg_s` | [keremberke/yolov8s-pcb-defect-segmentation](https://huggingface.co/keremberke/yolov8s-pcb-defect-segmentation) | dry_joint, incorrect_installation, pcb_damage, short_circuit |

Имена классов из **части** публичных датасетов дефектов трассировки нормализуются
к шести базовым кодам (`open` / `short` / `mousebite` / `spur` / `copper` / `pinhole`)
в `app/services/detector.py` (таблица `CLASS_ALIASES`). У весов с иной таксономией
коды могут остаться оригинальными; смотрите фактический список в `/api/meta`.

Переключать и удалять датасеты можно через админ-панель → вкладка «Датасеты»,
загружать свои `.pt` — там же.

### Вариант 2 (для продакшена): своё обучение

Поместите обученные веса YOLOv8 в файл, указанный переменной
`MODEL_WEIGHTS_PATH` (по умолчанию `models/yolov8_deeppcb.pt`), либо загрузите их
через админ-панель. Обучение на DeepPCB выполняется скриптом `scripts/train.py`
(шаблон). Инструкция подготовки датасета приведена в проектной документации
АОИ.01.51 — руководство системного программиста. Полезные ссылки на датасеты:

- DeepPCB: <https://github.com/tangsanli5201/DeepPCB>
- PKU-Market-PCB (HRIPCB): <https://robotics.pkusz.edu.cn/resources/datasetENG/>
- Kaggle (готовый YOLO-вариант): <https://www.kaggle.com/datasets/akhatova/pcb-defects>

### Fallback

Если ни активного датасета, ни файла по `MODEL_WEIGHTS_PATH` нет — приложение
автоматически переключается в режим **fallback-детектора** (эвристика на OpenCV).
Fallback предназначен **только для демонстрационных целей** и не удовлетворяет
количественным требованиям ТЗ (mAP@0.5 не менее 0,85).

## Роли и разграничение доступа (ТЗ п. 4.8.3)

| Роль | Возможности |
|---|---|
| Сотрудник (оператор) | Инспекция, своё устройство и журнал, экспорт своих протоколов |
| Руководитель | Полномочия оператора + общий журнал, статистика, датасеты, устройства, просмотр списка пользователей |
| Администратор | Полномочия руководителя + настройки системы, эталоны Golden Board, создание/правка/удаление пользователей, опасные операции (полная очистка журнала на сервере) |

Роль кодируется в JWT и проверяется зависимостями FastAPI (`require_manager`,
`require_admin` и др. в `app/api/deps.py`).

## Документация API

FastAPI генерирует полную интерактивную документацию автоматически:

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`

## Соответствие ТЗ

Сводная таблица соответствия кода требованиям ТЗ приведена в документе
«Описание программы» АОИ.01.13 (ГОСТ 19.402-78).

## Лицензия

Разработка выполнена в учебных целях в рамках дипломного проекта студента группы
ЭМт-221 Паксина Павла Петровича (КузГТУ им. Т.Ф. Горбачева, 2026 г.).
