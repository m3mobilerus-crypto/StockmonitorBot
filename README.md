# M3 Mobile — Stock Monitor

Сервис мониторинга остатков для Railway. Каждый час проверяет Treolan, OCS и M3 Stock и шлёт алерты в Telegram.

## 🚀 Деплой на Railway

### 1. Создай новый проект

1. Зайди на [railway.app](https://railway.app)
2. **New Project → Deploy from GitHub repo**
3. Загрузи эти файлы в новый GitHub репозиторий (например `m3-stock-monitor`)
4. Подключи репозиторий

### 2. Узнай свой Telegram Chat ID

Напиши боту [@userinfobot](https://t.me/userinfobot) — он ответит твоим Chat ID.

Или напиши любое сообщение своему боту, потом открой:
```
https://api.telegram.org/bot<ВАШ_ТОКЕН>/getUpdates
```
Найди `"chat":{"id": XXXXXXX}` — это и есть Chat ID.

### 3. Переменные окружения в Railway

В разделе **Variables** добавь:

| Переменная | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `123456:ABC-токен-от-BotFather` |
| `TELEGRAM_CHAT_ID` | `ваш_chat_id` |
| `TREOLAN_API` | `https://treolan-api-production.up.railway.app` |
| `OCS_API` | `https://ocs-api-production-de1e.up.railway.app` |
| `M3_STOCK_CSV` | URL к CSV файлу M3 стока |
| `DIFF_THRESHOLD` | `1` (порог изменения в штуках) |

### 4. Deploy

Railway автоматически задеплоит сервис. После деплоя:
- Сервис сразу сделает первую проверку (baseline — без алертов)
- Через час придут первые алерты при изменениях

## 📡 API эндпоинты

- `GET /` — статус сервиса и время последней проверки
- `POST /check` — принудительная проверка прямо сейчас

## 📬 Примеры алертов в Telegram

```
🔵 Появился в наличии [Treolan]
📦 MC3300AK-10B4E00RU
📝 Zebra MC3300 Android…
✅ Стало: 5 шт.

🟢 📈 Изменилось количество [OCS]
📦 TC220K-02B223RU
📝 Zebra TC22…
3 → 8 шт. (+5)

🟡 🚚 Новый транзит [M3]
📦 M3-SM15W-XXX
📝 M3 SM15W…
🔜 Транзит: 10 шт.
```

## ⚙️ Настройка интервала

По умолчанию проверка каждый **1 час**. Чтобы изменить — в `main.py` найди:
```python
scheduler.add_job(check_stocks, "interval", hours=1, id="stock_check")
```
Измени `hours=1` на нужное значение.
