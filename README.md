# AI Automation Builder

Telegram-бот, который превращает текстовые запросы в готовые автоматизации (n8n workflows). Опиши задачу — получи рабочую автоматизацию.

## Что умеет

- **QA**: отвечает на вопросы об автоматизации
- **Automation**: интерпретирует задачу → выбирает шаблон → генерирует n8n workflow → деплоит
- **Hybrid**: комбинирует ответ и автоматизацию

### Доступные шаблоны

| Шаблон | Описание |
|---|---|
| `telegram_to_sheets` | Сообщения из Telegram → Google Sheets |
| `telegram_to_ai_reply` | Telegram бот с AI-ответами |
| `form_to_notification` | Webhook/форма → Telegram уведомление |

## Стек

- Python 3.11+, python-telegram-bot 20
- LLM: OpenRouter (тест) / Anthropic Claude (прод)
- Автоматизации: n8n (self-hosted или cloud)

## Быстрый старт

```bash
# 1. Клонировать и установить зависимости
pip install -r requirements.txt

# 2. Настроить окружение
cp .env.example .env
# Заполнить .env (см. ниже)

# 3. Запустить бота
python bot/main.py
```

## Переменные окружения

```env
TELEGRAM_BOT_TOKEN=     # от @BotFather
OPENROUTER_API_KEY=     # console.openrouter.ai
ANTHROPIC_API_KEY=      # console.anthropic.com (для прода)
LLM_PROVIDER=openrouter # openrouter | anthropic
LLM_MODEL=openai/gpt-4o-mini

N8N_BASE_URL=http://localhost:5678
N8N_API_KEY=            # n8n → Settings → API → Create key
```

## Деплой

### Локально (бот + n8n одной командой)

```bash
cp .env.example .env   # заполнить токены
docker compose up -d
```

Бот и n8n поднимаются вместе. n8n доступен на `http://localhost:5678`.

### Только n8n (без Docker Compose)

```bash
docker run -d --name n8n -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n
```

После запуска: `http://localhost:5678` → Settings → API → создать ключ → вставить в `.env`.

### Облако

**Railway / Render / Heroku** — через `Procfile` (уже в репо):

1. Создать новый проект на [Railway](https://railway.app) или [Render](https://render.com)
2. Подключить GitHub репозиторий
3. Добавить все переменные из `.env.example` в настройках сервиса
4. Для `N8N_BASE_URL` — указать адрес своего n8n (Railway можно поднять как отдельный сервис из образа `n8nio/n8n`)
5. Deploy — бот запустится автоматически

**VPS (любой)** — через Docker:

```bash
git clone <repo> && cd AI-Workflow-Builder
cp .env.example .env  # заполнить
docker compose up -d
```

## Архитектура

```
User (Telegram)
    ↓
Router Agent       — qa / automation / hybrid
    ├── QA Agent           — отвечает на вопросы
    └── Interpreter Agent  — извлекает структуру задачи
              ↓
          Builder Agent    — выбирает шаблон, заполняет данные
              ↓
          n8n API          — создаёт и активирует workflow
```

## Этапы разработки

- [x] Этап 1 — Telegram бот + QA Agent
- [x] Этап 2 — Router + Interpreter
- [x] Этап 3 — Templates + Builder + n8n
- [ ] Этап 4 — Marketing Engine
- [ ] Этап 5 — Переход на Claude API (прод)
