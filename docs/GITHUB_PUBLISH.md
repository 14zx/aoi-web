# Публикация АОИ-Web на GitHub (исходники + релиз с portable)

Пошаговая инструкция для Windows. Репозиторий в git **ещё не привязан к GitHub**, пока вы сами не создадите проект на github.com и не выполните `git remote add`.

## 1. Что попадёт в git, а что — нет

| В репозитории | Не в репозитории (`.gitignore`) |
|---------------|----------------------------------|
| `app/`, `scripts/`, `tests/`, `alembic/`, `build/*.spec`, `build/launch_portable_https.bat` | `.venv/`, `storage/`, `logs/`, `*.db`, `.env` |
| `requirements.txt`, `README.md`, `docs/` | `build/portable_dist_*`, `build/pyinstaller_*` |
| `build_portable_https.bat`, `run_https.bat` | Готовый `.exe` и папка `_internal` (только в Release ZIP) |

## 2. Установить инструменты (один раз)

1. [Git for Windows](https://git-scm.com/download/win) — уже есть, если `git --version` работает.
2. [GitHub CLI](https://cli.github.com/) — для релизов из командной строки (`gh release create`).  
   После установки: `gh auth login`.

## 3. Первый коммит (если ещё не сделан)

В PowerShell из корня проекта (`diplome\diplome`):

```powershell
cd "C:\Users\Neizy\3D Objects\diplome\diplome"

git init
git add .
git status
git commit -m "АОИ-Web: исходники дипломного проекта"
```

## 4. Создать репозиторий на GitHub

1. github.com → **New repository**.
2. Имя, например: `aoi-web` или `diplome-aoi`.
3. **Без** README/license (они уже в проекте).
4. Скопируйте URL, например `https://github.com/ВАШ_ЛОГИН/aoi-web.git`.

Привязка и отправка:

```powershell
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/aoi-web.git
git push -u origin main
```

При запросе логина используйте [Personal Access Token](https://github.com/settings/tokens) (scope `repo`), не пароль от аккаунта.

### Вариант через GitHub CLI

```powershell
gh repo create aoi-web --private --source=. --remote=origin --push
```

(`--public`, если репозиторий открытый.)

## 5. Собрать portable для релиза

Нужны `.venv` с зависимостями и PyInstaller:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

Сборка (10–30 минут, PyTorch + YOLO):

```powershell
.\build_portable_https.bat portable_dist_release /SkipMigrate
```

Готовый каталог:

`build\portable_dist_release\AOI-Web-Portable-HTTPS\`

Запуск на стенде: `AOI-Web-Portable-HTTPS.exe` или `launch_portable_https.bat`.

## 6. Упаковать ZIP для Release

```powershell
.\scripts\package_release.ps1 -DistName portable_dist_release -Version 1.0.0
```

Файл появится в `dist\AOI-Web-Portable-HTTPS-1.0.0-win64.zip`.

## 7. Опубликовать Release на GitHub

С тегом (версия = тег):

```powershell
git tag -a v1.0.0 -m "Portable HTTPS, первая публикация"
git push origin v1.0.0

gh release create v1.0.0 `
  --title "АОИ-Web 1.0.0 (portable HTTPS)" `
  --notes "Portable-сборка для Windows. Распаковать ZIP и запустить AOI-Web-Portable-HTTPS.exe." `
  "dist\AOI-Web-Portable-HTTPS-1.0.0-win64.zip"
```

Без `gh` — вручную: репозиторий → **Releases** → **Draft a new release** → тег `v1.0.0` → прикрепить ZIP.

## 8. Обновление версии позже

```powershell
git add -A
git commit -m "Описание изменений"
git push

.\build_portable_https.bat portable_dist_release /SkipMigrate
.\scripts\package_release.ps1 -Version 1.1.0
git tag v1.1.0
git push origin v1.1.0
gh release create v1.1.0 --notes "..." "dist\AOI-Web-Portable-HTTPS-1.1.0-win64.zip"
```

## 9. Автосборка на GitHub (Actions)

В репозитории два workflow:

| Файл | Когда запускается | Что делает |
|------|-------------------|------------|
| `.github/workflows/ci.yml` | каждый push / PR в `main` | `pytest` (~5–15 мин) |
| `.github/workflows/portable-windows.yml` | push в `main`, тег `v*` или вручную | PyInstaller + ZIP (~20–40 мин) |

После push откройте вкладку **Actions** на GitHub:

- **CI** — зелёная галочка = тесты прошли.
- **Portable Windows** — в конце job **Artifacts** → `portable-win64` (ZIP со сборкой).

**Релиз автоматически:** при push тега `v1.0.0` workflow создаёт **GitHub Release** и прикрепляет ZIP.

```powershell
git tag v1.0.0
git push origin v1.0.0
```

Ручной запуск без тега: **Actions** → **Portable Windows** → **Run workflow**.

Ограничения:

- Сборка только **Windows** (как локальный `build_portable_https.bat`).
- Учитывается лимит минут GitHub (для private-репозиториев — по тарифу).
- Веса `.pt` в git не хранятся. Перед portable: заполните `models/` (см. [models/README.md](../models/README.md)), затем `.\scripts\package_models_release.ps1` и прикрепите **AOI-Web-models-*.zip** к отдельному Release.

Первый запуск portable на новом репозитории: убедитесь, что workflow-файлы уже в `main` (`git push`).

## Частые проблемы

- **`remote origin already exists`** — `git remote set-url origin НОВЫЙ_URL`.
- **Большой push** — в git не должны попадать `build/portable_dist_*` и `.venv`; проверьте `git status`.
- **Нет весов модели** — в portable положите `models\*.pt` в `_internal\models` до упаковки или скачайте через админку после первого запуска.
