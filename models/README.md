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

1. Скачайте **`AOI-Web-models-1.0.0.zip`** из [Releases](https://github.com/14zx/aoi-web/releases)
   (тег **`v1.0.0-models`** или *Assets* у релиза *models*).
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

### GitHub Actions (portable **с весами**)

Workflow **Portable Windows** перед PyInstaller вызывает `scripts/ci_install_models.py`:

1. Пытается скачать ZIP с Release **`v1.0.0-models`** (`AOI-Web-models-1.0.0.zip`).
2. Или URL из переменной репозитория **`MODELS_BUNDLE_URL`** (Settings → Secrets and variables → Actions → Variables).
3. Если архива нет — качает только публичные пресеты с Hugging Face (без `aoi_unified.pt`).

**Один раз** выложите полный архив:

```powershell
.\scripts\package_models_release.ps1 -Version 1.0.0
```

GitHub → **Releases** → **New release** → tag `v1.0.0-models` → прикрепить `dist\AOI-Web-models-1.0.0.zip`.

После push в `master` артефакт **`portable-win64-with-models`** (~500+ МБ) появится в **Actions** → успешный run → **Artifacts**.

## Проверка

```bash
python -m scripts.verify_models
```

Скрипт сверяет наличие файлов из `manifest.yaml` и печатает отсутствующие.
