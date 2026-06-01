# Единая модель детекции и дообучение

Этот документ описывает, как собрать один детектор, который покрывает
бездефектную PCB, дефекты пайки и монтаж компонентов, и как поддерживать
его дообучение на оценках оператора.

## Архитектура пайплайна

```
models/unified_classes.yaml       единственный источник правды о классах
        │
        ├─►  scripts/prepare_datasets.py    публичные датасеты → datasets/unified/
        ├─►  scripts/aggregate_reviews.py   оценки оператора  → datasets/unified/
        │
        └─►  scripts/train_unified.py       YOLOv8 train/finetune
                         │
                         └─►  models/aoi_unified.pt     веса, которые цепляет
                                                        app.services.detector
```

Каждый шаг — отдельный Python-скрипт, можно запускать по частям.

## Реестр классов

Все инструменты читают `models/unified_classes.yaml`. В нём 10 классов:

| id | code                   | Категория  | Описание                                  |
|----|------------------------|------------|-------------------------------------------|
| 0  | `open`                 | defect     | Обрыв дорожки                             |
| 1  | `short`                | defect     | Короткое замыкание                        |
| 2  | `mousebite`            | defect     | Мышиный укус                              |
| 3  | `spur`                 | defect     | Медная шпора                              |
| 4  | `copper`               | defect     | Паразитная медь                           |
| 5  | `pinhole`              | defect     | Пропущенное отверстие                     |
| 6  | `solder_bridge`        | solder     | Перемычка припоя                          |
| 7  | `solder_cold`          | solder     | Холодная пайка / непрогретый стык         |
| 8  | `component_missing`    | component  | Отсутствие компонента / tombstoning       |
| 9  | `component_misaligned` | component  | Смещение или неправильная ориентация      |

**Важно:** порядок `0..5` совпадает с прежним хардкодом `DEFECT_CLASSES` —
это гарантирует, что ранее сохранённые инспекции и их `labels.txt` остаются
валидными. Новые классы допустимо добавлять только в конец.

Включение расширенного реестра в приложении:

```powershell
# PowerShell
$env:USE_UNIFIED_CLASSES = "1"
python -m uvicorn app.main:app --reload
```

Или в `.env`:

```
USE_UNIFIED_CLASSES=1
```

Без флага детектор работает по старым 6 классам (совместимо с существующими
весами).

## 1. Подготовка публичных датасетов

```powershell
pip install datasets huggingface_hub   # разово

python scripts/prepare_datasets.py --dry-run
python scripts/prepare_datasets.py
```

Что делает скрипт:
1. читает `models/unified_classes.yaml`;
2. скачивает перечисленные в `SOURCES` источники (HuggingFace-датасеты и/или
   локальные YOLO-ZIP);
3. переводит разметку в единый `class_id` через `aliases` и явные `class_map`;
4. кладёт всё в `datasets/unified/` + генерирует `data.yaml`.

Добавить свой датасет (например, Roboflow YOLOv8 export):

1. положить ZIP в `datasets/_cache/my_dataset.zip`;
2. в `scripts/prepare_datasets.py`, в `SOURCES`, раскомментировать / добавить
   запись вида:
   ```python
   Source(
       key="my_dataset",
       title="...",
       kind="yolo_zip",
       path=CACHE_DIR / "my_dataset.zip",
       class_map={"имя_в_zip": "код_в_unified", ...},
   )
   ```
3. перезапустить `python scripts/prepare_datasets.py`.

Итоги подготовки:
* `datasets/unified/_stats.json` — счётчик образцов на класс;
* `datasets/unified/data.yaml` — конфиг для Ultralytics;
* непознанные имена классов логируются с подсказкой добавить их в `aliases`.

## 2. Агрегация оценок оператора

Каждая ручная проверка сохраняет в `storage/training/<inspection_id>/` YOLO-совместимые
файлы (`original.*`, `labels.txt`, `annotations.json`). Чтобы влить их в обучающий
датасет:

```powershell
python scripts/aggregate_reviews.py --dry-run
python scripts/aggregate_reviews.py            # все проверенные инспекции
python scripts/aggregate_reviews.py --wipe     # сначала удалить прошлые
python scripts/aggregate_reviews.py --min-inspection 120
```

Скрипт:
* автоматически сплитит train/val тем же хеш-алгоритмом, что и `prepare_datasets.py`
  (одна и та же инспекция стабильно попадает в один сплит);
* ремапит `class_id` через `annotations.json` + `unified_classes.yaml`;
* корректно обрабатывает пустые `labels.txt` (это valid «negative»-пример).

## 3. Обучение

Окружение: PyTorch с CUDA (для RTX 3050 ставится стандартной командой
Ultralytics/PyTorch). Проверить, что GPU виден:

```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Первый прогон с нуля:

```powershell
python scripts/train_unified.py --epochs 100 --batch 16
# при нехватке VRAM:
python scripts/train_unified.py --epochs 100 --batch 8 --model yolov8n.pt
```

После обучения `best.pt` копируется в `models/aoi_unified.pt`. Чтобы детектор его
подхватил:

```
$env:AOI_MODEL_WEIGHTS_PATH = "models\aoi_unified.pt"
$env:USE_UNIFIED_CLASSES = "1"
```

Или через админку: вкладка «Датасеты» → загрузить `aoi_unified.pt` и активировать.

## 4. Дообучение на новых оценках оператора

Целевой цикл «пятница вечером»:

```powershell
# 1. собрать новые оценки
python scripts/aggregate_reviews.py

# 2. дообучить поверх текущей модели (короткие 20–30 эпох хватает)
python scripts/train_unified.py --from models\aoi_unified.pt --epochs 30
```

Флаг `--from` говорит Ultralytics взять ваши текущие веса в качестве стартовых,
а не `yolov8s.pt`. Валидация сравнивает метрики на том же `data.yaml`.

## 5. Ограничения

* Ориентация и объём припоя не решаются одним детектором. Под них оставлены
  обобщённые классы `component_misaligned` / `solder_cold` — если понадобится
  более тонкая классификация, добавляйте классы в конец `unified_classes.yaml`
  и переобучайте (id прошлых классов не меняются).
* Соответствие типа корпуса (package conformity) — это BOM-сверка: детектор даёт
  локализации компонентов, сравнение с ведомостью делается поверх.
* Классы `component_*` в публичных источниках редки. Основной путь наполнения —
  оценки оператора через ревью-workflow.

## 6. Файлы, которых этот пайплайн касается

* `models/unified_classes.yaml`
* `scripts/prepare_datasets.py`
* `scripts/aggregate_reviews.py`
* `scripts/train_unified.py`
* `app/services/detector.py` — чтение реестра при старте (`USE_UNIFIED_CLASSES=1`)
* `app/config.py` — настройки `use_unified_classes` и `unified_classes_path`
