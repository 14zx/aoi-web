# Дорожная карта модернизации АОИ (по ТЗ)

Статусы: **done** | **in_progress** | **planned**.

## Этап 0 — Документация предобработки (п. 3.x)

- **done** — `docs/TZ_section3_preprocessing.md`, код `app/services/preprocessing.py`, тесты.

## Этап 1 — Аппаратная синхронизация (п. 3: команда освещения → захват)

- **done** (mock) — программный шлюз «сервер ↔ МК/исполнитель» без привязки к конкретному UART:
  - `app/services/hardware_gateway.py` — пресеты `white_diffuse`, `rgb_highlight`, `off`, журнал команд, mock по умолчанию; `hardware_transport` в `Settings`.
  - `app/api/pipeline.py` — `POST /api/pipeline/lighting/preset`, `POST /api/pipeline/capture/ack`, `GET /api/pipeline/hardware/status`.
- **planned** — реальный транспорт (pyserial), таймауты ACK, привязка к сессии инспекции.

## Этап 2 — Выравнивание кадра (Image Alignment, ECC)

- **done** — `app/services/alignment.py` — `align_rgb_ecc`; настройки `alignment_ecc_*`; `POST /api/pipeline/alignment/demo`.
- **done** — `app/services/golden_alignment.py` + форма `golden_board_profile_id`: выравнивание перед детекцией в `POST /api/inspections` и `POST /api/inspections/live` (контракт JSON: `reference_image_rel` или `reference.image_rel` к файлу в ``storage/``).
- **planned** — маска ROI, SIFT как fallback.

## Этап 3 — Golden Board Manager (п. 5, backend)

- **done** — таблица `golden_board_profiles`, CRUD (только руководитель), загрузка опорного снимка `POST .../reference-image`, разметка `PUT .../markup`, выдача снимка `GET .../reference-image`; вкладка «Эталоны» в SPA (рамки мышью).
- **done** — сверка `regions` с детекциями YOLO после ECC (`app/services/golden_region_check.py`): дефекты `golden_component_missing`, `golden_component_wrong`; допуск `golden_region_tolerance_px`; авторазметка при загрузке (`golden_auto_markup.py`).
- **planned** — экспорт YOLO-лейблов из разметки, версии эталона, подписи классов на canvas.

## Этап 4 — Каскад после YOLO (кропы, узкие классификаторы)

- **planned** — очередь стадий, очередь кропов, плагины дефектов (missing, polarity, …).

## Этап 5 — Дефекты по п.4 (логика + обучение)

- **planned** — Golden vs YOLO missing, OCR/классификация корпуса, геометрия смещения, мосты, RGB-серия кадров.

## Этап 6 — Интеграция в основной протокол инспекции

- **done** — поля протокола `golden_board_profile_id`, `golden_alignment_used`, `alignment_mae_before/after`; ECC перед YOLO; сверка regions; UI (id профиля, допуск px, вкладка «Эталоны» с авторазметкой).
- **planned** — полный сценарий «стадия N», JSON sidecar, жёсткая привязка к сессии захвата.
