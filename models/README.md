# Каталог весов моделей

Файлы `*.pt` **не коммитятся** в репозиторий (большой размер). В Git хранятся
манифест, реестры классов и структура каталогов.

## Основная модель (релиз / portable)

| Файл | Назначение |
|------|------------|
| `datasets/7/weights.pt` | Рабочие веса YOLO: **детекция компонентов** на плате |
| `unified_classes.yaml` | Расширенный реестр для обучения / legacy |
| `primary_model_classes.yaml` | Описание классов основной модели и кодов дефектов сборки |

Классы YOLO (9): `smd_capacitor`, `diode`, `ec`, `ic`, `led`, `smd_resistor`,
`scapacitor`, `zener`, `smd_pad`.

Дополнительные коды брака при инспекции с эталоном: `golden_component_missing`,
`golden_component_wrong`, `golden_polarity_wrong`, `placement_tilt`.

## Установка весов

1. Скопируйте `models/datasets/7/weights.pt` (с носителя / архива диплома).
2. Проверка: `python -m scripts.verify_models`

Архив для переноса: упакуйте вручную каталог `models/` (достаточно
`models/datasets/7/weights.pt` + `unified_classes.yaml` + `manifest.yaml`).

## Настройка `.env`

```env
MODEL_WEIGHTS_PATH=models/datasets/7/weights.pt
```

## Portable (PyInstaller)

Перед `build_portable_https.bat` положите `datasets/7/weights.pt` в `models/`.
Spec копирует каталог `models/` в `_internal/models/`.
