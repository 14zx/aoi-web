# Подсветка стенда через WLED (JSON API)

АОИ-Web управляет контроллером **WLED** по локальной сети. Настройки задаются на сайте (вкладка «Инспекция» → «Настройки подключения WLED», только администратор) и хранятся в БД (`wled_hardware`).

Документация WLED: https://kno.wled.ge/interfaces/json-api/

## Поиск устройства в сети

Как в официальном приложении WLED:

1. **mDNS** — сервис `_wled._tcp` (Bonjour/Zeroconf), кнопка «Найти WLED в сети» (только администратор).
2. **GET `/json/nodes`** — если указан хотя бы один адрес WLED, прошивка отдаёт соседние экземпляры.
3. **Вручную** — IP/URL в поле адреса (резерв).

Режим «Авто-поиск» при сохранении без адреса запускает поиск и подставляет первое доступное устройство.

## Настройка в интерфейсе

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| Адрес | — | Например `http://192.168.50.130` |
| Путь статуса (GET) | `/json/info` | Проверка связи, имя устройства, число LED |
| Путь состояния (POST) | `/json/state` | Управление яркостью, цветом, вкл/выкл |
| ID сегмента | `0` | Сегмент полоски (обычно 0) |
| Переход | `7` | Длительность смены, единица 100 мс (7 ≈ 700 мс) |

Кнопка «Проверить связь» выполняет `GET {base_url}/json/info`.

## Что отправляет сервер на WLED

Пример включения с цветом (эффект Solid, `fx: 0`):

```bash
curl -X POST "http://192.168.50.130/json/state" \
  -H "Content-Type: application/json" \
  -d '{"on":true,"bri":204,"transition":7,"v":true,"seg":[{"id":0,"sel":true,"fx":0,"col":[[255,230,233],[0,0,0],[0,0,0]]}]}'
```

Выключение:

```json
{"on": false, "v": true}
```

Пресеты UI:

| Пресет | Поведение |
|--------|-----------|
| `white_diffuse` | Вкл., белый цвет, яркость с ползунка |
| `rgb_highlight` | Вкл., цвет с палитры `#RRGGBB`, яркость с ползунка |
| `off` | `on: false` |

Яркость в UI — 0–100 %, на WLED переводится в `bri` 0–255.

## Эндпоинты АОИ-Web (JWT: operator / manager / admin)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/pipeline/hardware/status` | Статус WLED + активный пресет |
| POST | `/api/pipeline/hardware/probe` | Принудительная проверка связи |
| POST | `/api/pipeline/lighting/control` | `{ "preset?", "brightness?", "color?" }` |
| POST | `/api/pipeline/lighting/preset` | Только пресет |
| GET/PUT | `/api/pipeline/hardware/config` | Настройки (только **admin**) |
| POST | `/api/pipeline/hardware/discover` | Поиск WLED в LAN (**admin**) |
| GET | `/api/pipeline/hardware/admin/diagnostics` | Журнал запросов, `last_wled_state`, info (**admin**) |
| POST | `/api/pipeline/hardware/admin/debug-request` | Тестовый GET/POST на `/json/…` (**admin**) |

## Миграция со старого ESP32 API

Если в БД остались пути `/health` и `/api/lighting/control`, при сохранении они автоматически заменяются на `/json/info` и `/json/state`.

Ключ настроек в БД: `wled_hardware` (старый `esp32_hardware` переносится при первом сохранении).
