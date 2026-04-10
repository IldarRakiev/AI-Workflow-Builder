# AI Automation Builder — Project Context

---

## Что это за проект

Telegram-бот, который принимает текстовые запросы на естественном языке и:
- Отвечает на вопросы (QA)
- Генерирует и деплоит автоматизации в n8n (automation)
- Делает и то, и другое (hybrid)

Интерфейс — только Telegram. Никакого dashboard, форм, UI.

---

## Стек

| Слой | Технология |
|---|---|
| Interface | Telegram Bot (python-telegram-bot 20.7) |
| Backend | Python 3.11+ |
| LLM (тест) | OpenAI GPT-4o-mini через OpenRouter |
| LLM (прод) | Anthropic Claude Sonnet / Opus (API) |
| Automation | n8n (self-hosted через Docker) |
| Env | .env через python-dotenv |
| Deploy | Docker Compose (бот + n8n), Railway/Render (облако) |

---

## Текущая структура файлов

```
AI-Workflow-Builder/
├── config.py               # env vars, валидация при импорте
├── bot/
│   └── main.py             # точка входа бота, оркестрация агентов
├── agents/
│   ├── router.py           # Router Agent
│   ├── qa.py               # QA Agent
│   ├── interpreter.py      # Interpreter Agent
│   └── builder.py          # Builder Agent
├── templates/
│   ├── telegram_to_sheets.json
│   ├── telegram_to_ai_reply.json
│   └── form_to_notification.json
├── utils/
│   ├── llm.py              # LLM обёртка (OpenRouter / Anthropic)
│   └── n8n.py              # n8n API клиент
├── Dockerfile
├── Procfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── docs/
    ├── PROJECT_CONTEXT.md  # этот файл
    └── Stages 4-9.md       # детальный план следующих этапов
```

---

## Переменные окружения (.env)

```env
TELEGRAM_BOT_TOKEN=         # обязательно
OPENROUTER_API_KEY=         # обязательно при LLM_PROVIDER=openrouter
ANTHROPIC_API_KEY=          # обязательно при LLM_PROVIDER=anthropic
LLM_PROVIDER=openrouter     # openrouter | anthropic
LLM_MODEL=openai/gpt-4o-mini
N8N_BASE_URL=http://localhost:5678
N8N_API_KEY=                # опционально, бот работает без n8n
```

---

## Архитектура агентов и поток данных

```
User message (Telegram)
    │
    ▼
bot/main.py::handle_message()
    │
    ├── router.classify() → RouteResult {type, confidence, intent}
    │
    ├── type="qa"          → qa.answer() → str
    │
    ├── type="automation"  → interpreter.extract() → InterpreterResult
    │                            │
    │                            ▼
    │                       builder.generate() → BuilderResult
    │                            │
    │                            ▼
    │                       n8n.deploy() + n8n.activate()
    │
    └── type="hybrid"      → qa.answer() + _handle_automation() параллельно
```

---

## Модули: сигнатуры и типы

### config.py
```python
TELEGRAM_BOT_TOKEN: str
OPENROUTER_API_KEY: str
ANTHROPIC_API_KEY: str
LLM_PROVIDER: str        # "openrouter" | "anthropic"
LLM_MODEL: str           # "openai/gpt-4o-mini" по умолчанию
N8N_BASE_URL: str        # "" если не задан
N8N_API_KEY: str         # "" если не задан
```

### utils/llm.py
```python
async def ask(messages: list[dict], system: str = "") -> str
def parse_json_response(raw: str) -> dict
# Провайдер выбирается через LLM_PROVIDER env var
# OpenRouter: AsyncOpenAI с base_url="https://openrouter.ai/api/v1"
# Anthropic: anthropic.AsyncAnthropic (SDK импортируется условно)
```

### agents/router.py
```python
class RouteResult(TypedDict):
    type: str          # "qa" | "automation" | "hybrid"
    confidence: float  # 0.0–1.0
    intent: str

async def classify(user_message: str, history: list[dict] | None = None) -> RouteResult
# fallback: {"type": "qa", "confidence": 0.0, "intent": "classification failed"}
# confidence < 0.6 → принудительно "qa"
```

### agents/interpreter.py
```python
class TaskStructure(TypedDict):
    trigger: str           # "новое сообщение в Telegram-группе"
    actions: list[str]     # ["получить сообщение", "сохранить в Sheets"]
    destination: str       # "Google Sheets" | "unknown"
    entities: dict         # {"service": "Telegram", "sheet_id": "..."}

class InterpreterResult(TypedDict):
    task: TaskStructure
    summary: str           # дружелюбное подтверждение на языке пользователя

async def extract(user_message: str, history: list[dict] | None = None) -> InterpreterResult
```

### agents/builder.py
```python
class BuilderResult(TypedDict):
    template_id: str           # "telegram_to_sheets" | "telegram_to_ai_reply" |
                               # "form_to_notification" | "custom" | "error"
    workflow_json: dict        # готовый n8n workflow
    filled_placeholders: dict  # {"BOT_TOKEN": "...", "CHAT_ID": "PENDING_CHAT_ID"}
    summary: str               # сообщение пользователю

async def generate(task: TaskStructure, user_message: str = "") -> BuilderResult
def get_pending_guides(fills: dict[str, str]) -> str
# Автозаполнение: BOT_TOKEN, AI_API_KEY, AI_API_URL, AI_MODEL берутся из .env
# PENDING_* — плейсхолдеры, которые нужно заполнить вручную
# get_pending_guides() возвращает понятные инструкции для каждого PENDING
```

### utils/n8n.py
```python
async def health_check() -> bool      # GET /healthz, timeout 5s
async def deploy(workflow_json: dict) -> dict   # POST /api/v1/workflows → {id, name}
async def activate(workflow_id: str) -> bool    # PATCH /api/v1/workflows/{id}
```

### agents/qa.py
```python
async def answer(user_message: str, history: list[dict] = None) -> str
# System prompt: помощник AI Automation Builder, отвечает на языке пользователя
```

### bot/main.py
```python
# Состояние:
_history: dict[int, list[dict]]  # per-user история, макс 10 сообщений
MAX_HISTORY = 10

# Handlers:
# /start, /help, text → handle_message()
# handle_message() — router → qa / _handle_automation() / hybrid

# _handle_automation():
# 1. interpreter.extract()
# 2. builder.generate()
# 3. n8n.health_check() → если доступен: deploy + activate
# 4. builder.get_pending_guides() → инструкции для незаполненных полей
```

---

## Шаблоны n8n

Все шаблоны в `/templates/*.json`, используют плейсхолдеры `{{PLACEHOLDER}}`.

| Шаблон | Плейсхолдеры |
|---|---|
| `telegram_to_sheets` | BOT_TOKEN ✅ auto, SHEET_ID ⏳, SHEET_NAME ⏳ |
| `telegram_to_ai_reply` | BOT_TOKEN ✅, AI_API_URL ✅, AI_API_KEY ✅, AI_MODEL ✅, CHAT_ID ⏳ |
| `form_to_notification` | WEBHOOK_PATH ⏳, BOT_TOKEN ✅, CHAT_ID ⏳ |

✅ = автозаполняется из .env | ⏳ = требует ввода от пользователя (с гайдом)

---

## Соглашения кодовой базы

1. **LLM вызовы только через `utils/llm.py::ask()`** — никогда не импортировать OpenAI/Anthropic SDK напрямую из агентов
2. **Все агенты** — отдельные модули в `agents/`, возвращают TypedDict или str
3. **Промпты** — константы на уровне модуля (`SYSTEM_PROMPT = "..."`)
4. **Логирование** — `logging.getLogger(__name__)`, не `print`
5. **Fallback** — все агенты перехватывают исключения и возвращают безопасный fallback
6. **Типизация** — `list[dict]` (Python 3.11+, не `List[Dict]`)
7. **Async throughout** — все агенты и хендлеры async/await

---

## Роадмап (что предстоит)

### Этап 4: Claude API + Выбор модели
- `utils/model_config.py` — per-user выбор модели (сохраняется в `data/user_models.json`)
- `/model` команда → InlineKeyboard: GPT-4o mini, GPT-4o, Claude Sonnet, Claude Opus
- `utils/llm.py`: добавить `model_override`, `provider_override` в `ask()`
- Все агенты: добавить `ask_kwargs: dict = {}`, передавать в `ask(**ask_kwargs)`
- Добавить `anthropic>=0.25.0` в requirements.txt

### Этап 5: Биллинг
- `utils/billing.py` — OpenRouter Keys API (create/balance/deactivate)
- `utils/workflows_db.py` — реестр деплоев пользователя (`data/user_workflows.json`)
- `utils/payments.py` — Telegram Payments (Stripe/Stars)
- Схема: пользователь платит нам → мы создаём OpenRouter-ключ с лимитом → ключ в workflows
- Fallback-ключ: gpt-4o-mini, активируется при нуле основного ключа
- `/services`, `/topup`, `/billing` команды

### Этап 6: Agent Networks
- `agents/network.py` — оркестратор: pipeline и parallel паттерны
- Пресеты: анализ рынка, маркетинговая стратегия, глубокий ресёрч
- Новый тип роута `"deep_task"` в router.py
- Прогресс в реальном времени — редактирование сообщения Telegram
- `/network` команда с выбором пресета
- Заменяет отдельный Marketing Engine

### Этап 7: Память + Документы + Голос
- `utils/memory.py` — per-user память в JSON, инжектируется в system prompt
- `utils/media.py` — транскрипция .ogg (Whisper), PDF/Excel/DOCX → текст, vision для фото
- Обработчики: голосовые, документы, фото

### Этап 8: RAG + Маркетплейс + СНГ-интеграции
- `utils/rag.py` — ChromaDB per-user, embeddings через OpenRouter
- `utils/marketplace.py` — GitHub registry шаблонов, установка в один клик
- `templates/cis/` — 6 шаблонов: Bitrix24, amoCRM, Wildberries, Ozon, VK, hh.ru

### Этап 9: MCP + Облачный деплой + Браузерные агенты
- `agents/mcp_n8n.py` — MCP сервер для мониторинга/правки n8n схем
- `utils/cloud.py` — деплой n8n на Railway/Render через API
- `agents/browser_agent.py` — Playwright + LLM-планировщик (MAX_STEPS=10)
- `utils/job_queue.py` — asyncio background jobs + Telegram уведомления

---

## Ключевое УТП

Единая точка входа через Telegram для всего спектра AI-задач:
от простого вопроса → до агентской сети → до деплоя автоматизации в облако.

Без кода. Без UI. Только чат.
