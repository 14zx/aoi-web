# Веса нейросетевых моделей (отдельно от Git)

Файлы `*.pt` и архивы весов **не хранятся в основном репозитории** на GitHub
(лимит 100 МБ на файл, общий размер ~150+ МБ). Конфигурации (`*.yaml`, `*.json`)
и этот каталог **в Git есть** — структура и инструкции.

## Структура каталога

```
models/
├── README.md                 # этот файл
├── manifest.yaml             # список ожидаемых файлов (для проверки)
├── unified_classes.yaml      # реестр классов (в Git)
├── aoi_unified.pt            # основная обученная модель проекта (вне Git)
├── yolov8_deeppcb.pt         # опционально, путь по умолчанию в .env
├── checkpoints/              # базовые чекпоинты для обучения (вне Git)
├── datasets/<id>/weights.pt  # пресеты из download_datasets (вне Git)
└── pretrained/<name>/*.pt    # публичные веса HuggingFace (вне Git)
```

## Способ 1 — архив весов проекта (рекомендуется для стенда / portable)

1. Скачайте **`AOI-Web-models.zip`** из [Releases](https://github.com/14zx/aoi-web/releases)
   (раздел *Assets* у последнего релиза с пометкой *models*).
2. Распакуйте **в корень проекта** (`diplome/`), чтобы появилась папка `models/` с `.pt`.
3. Проверка:

```bash
python -m scripts.verify_models
```

Локально архив можно собрать для выкладки в Release:

```powershell
.\scripts\package_models_release.ps1
```

## Способ 2 — `models.rar` (если передали на флешке / с диплома)

Распакуйте `models.rar` из корня репозитория так, чтобы файлы оказались в `models/`
(не во вложенную `models/models/`).

## Способ 3 — скачать из интернета

Публичные пресеты (Hugging Face):

```bash
pip install huggingface_hub
python -m scripts.download_pretrained
python -m scripts.download_datasets --only pku
```

Активный датасет переключается в админке → **Датасеты**.

## Настройка в `.env`

```env
MODEL_WEIGHTS_PATH=models/aoi_unified.pt
```

или другой `.pt` после распаковки / скачивания.

## Portable-сборка (PyInstaller)

Перед `build_portable_https.bat` убедитесь, что `models/` заполнен — spec
копирует каталог целиком в `_internal/models/`.

## Проверка

```bash
python -m scripts.verify_models
```

Скрипт сверяет наличие файлов из `manifest.yaml` и печатает отсутствующие.
