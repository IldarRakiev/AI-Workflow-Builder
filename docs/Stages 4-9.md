# AI Automation Builder — Полный роадмап (Этапы 4–9)

## Context

Этапы 1–3 готовы: Telegram бот, QA, Router, Interpreter, Builder, n8n интеграция, 3 шаблона.
Этот план охватывает все следующие этапы развития платформы: от выбора модели до браузерных агентов.

Ключевые договорённости из обсуждения:
- Интерфейс только через Telegram (никакого dashboard/UI)
- Все LLM-вызовы через `utils/llm.py`
- Marketing Engine убран — его функции покрывает Agent Networks (Этап 6)
- Биллинг: мы ресейлим OpenRouter-токены с маржой, пользователь платит нам

---

## Этап 4: Claude API + Выбор модели

**Зачем:** перейти на Claude в проде, дать пользователю выбирать модель через кнопки в боте.

### Новые файлы
- `utils/model_config.py` — хранит выбор модели per-user в `data/user_models.json`
  ```python
  class ModelConfig(TypedDict):
      provider: str   # "openrouter" | "anthropic"
      model: str
  
  def get_user_model(user_id: int) -> ModelConfig
  def set_user_model(user_id: int, provider: str, model: str) -> None
  ```

### Изменения в существующих файлах

**`utils/llm.py`**
- Добавить `model_override` и `provider_override` в `ask()`:
  ```python
  async def ask(messages, system="", *, model_override=None, provider_override=None) -> str
  ```
- `_ask_openrouter` и `_ask_anthropic` принимают `model` параметр

**Все агенты** (`router.py`, `interpreter.py`, `builder.py`, `qa.py`)
- Добавить `ask_kwargs: dict = {}` в публичные функции
- Передавать `**ask_kwargs` в каждый вызов `ask()`

**`bot/main.py`**
- Хелпер `_get_ask_kwargs(user_id) -> dict` → возвращает `{model_override, provider_override}`
- Передавать `ask_kwargs` во все вызовы агентов
- Новая команда `/model` → InlineKeyboard с моделями:
  ```
  [GPT-4o mini]  [GPT-4o]
  [Claude Sonnet (OR)]  [Claude Opus (OR)]
  [Claude Sonnet (Direct)]
  ```
- `CallbackQueryHandler` с паттерном `^model:` → `set_user_model()`

**`config.py`**
- Без изменений (anthropic SDK уже поддерживается в `llm.py`)

### Dependencies
```
anthropic>=0.25.0   # добавить в requirements.txt
```

### Новые команды
- `/model` — выбор модели через inline-кнопки, сохраняется в `data/user_models.json`

---

## Этап 5: Биллинг

**Зачем:** монетизация. Пользователь платит → получает OpenRouter ключ с лимитом.

### Новые файлы
- `utils/billing.py` — работа с OpenRouter Keys API:
  ```python
  async def create_user_key(user_id: int, budget_usd: float) -> dict  # {key, hash}
  async def get_key_balance(key_hash: str) -> float
  async def deactivate_key(key_hash: str) -> bool
  # Хранит записи в data/user_keys.json
  ```
- `utils/workflows_db.py` — реестр деплоев:
  ```python
  class WorkflowRecord(TypedDict):
      workflow_id: str
      template_id: str
      name: str
      active: bool
      created_at: str
  # data/user_workflows.json → {user_id: [WorkflowRecord]}
  def add_workflow(user_id, record) -> None
  def get_workflows(user_id) -> list[WorkflowRecord]
  def update_workflow_status(user_id, workflow_id, active: bool) -> None
  ```
- `utils/payments.py` — Telegram Payments (Stripe/Stars):
  ```python
  def build_invoice(user_id, amount_stars, description) -> dict
  def verify_payment(pre_checkout_query) -> bool
  ```

### Изменения в существующих файлах

**`config.py`** — добавить:
```python
OPENROUTER_ADMIN_KEY = os.getenv("OPENROUTER_ADMIN_KEY", "")  # master key для Keys API
TELEGRAM_PAYMENT_PROVIDER_TOKEN = os.getenv("TELEGRAM_PAYMENT_PROVIDER_TOKEN", "")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "openai/gpt-4o-mini")
```

**`utils/llm.py`**
- Добавить `user_api_key: str | None = None` в `ask()`
- Когда задан — использует его вместо `OPENROUTER_API_KEY`
- При ошибке баланса (HTTP 402) → fallback на `FALLBACK_MODEL` с системным ключом (тихо, без уведомления)

**`bot/main.py`**
- После n8n deploy → `workflows_db.add_workflow()`
- `PreCheckoutQueryHandler` + `MessageHandler(filters.SUCCESSFUL_PAYMENT)` → `billing.create_user_key()`
- Новые команды: `/services`, `/topup`, `/billing`

**`utils/n8n.py`** — добавить:
```python
async def deactivate(workflow_id: str) -> bool
async def list_workflows() -> list[dict]
```

### Новые команды
- `/services` — список workflows пользователя со статусом, кнопки вкл/выкл
- `/topup` — тарифы подписки + разовое пополнение через Telegram Payments
- `/billing` — текущий баланс ключа

### Схема биллинга
```
Подписка: фикс. ключ с месячным лимитом, автопродление
On-demand: пополнение → новый/пополненный ключ с лимитом
Fallback-ключ: создаётся при регистрации, gpt-4o-mini, активируется при нуле основного
```

---

## Этап 6: Agent Networks

**Зачем:** сложные задачи (анализ рынка, стратегия, ресёрч) — несколько агентов усиливают ответ.
Заменяет отдельный Marketing Engine — маркетинг = один из пресетов сети.

### Новые файлы
- `agents/network.py` — оркестратор:
  ```python
  class AgentSpec(TypedDict):
      name: str
      system_prompt: str

  class NetworkPreset(TypedDict):
      id: str
      name: str
      pattern: str   # "pipeline" | "parallel"
      agents: list[AgentSpec]

  # Константы-пресеты:
  NETWORK_MARKET_ANALYSIS: NetworkPreset  # researcher → analyst → critic → writer
  NETWORK_MARKETING_STRATEGY: NetworkPreset  # researcher → strategist → content → critic
  NETWORK_DEEP_RESEARCH: NetworkPreset  # parallel researchers → synthesizer → editor

  async def run_pipeline(preset, user_input, progress_cb, ask_kwargs={}) -> str
  async def run_parallel(preset, user_input, progress_cb, ask_kwargs={}) -> str
  ```
  - `progress_cb` — async callable, редактирует сообщение в Telegram в реальном времени
  - Каждый агент = `ask()` с system_prompt из `AgentSpec`, без отдельных `.py` файлов

### Изменения в существующих файлах

**`agents/router.py`**
- Добавить `"deep_task"` в `_VALID_TYPES`
- Обновить system prompt с примерами для `deep_task`

**`bot/main.py`**
- `_handle_deep_task()` — отправляет прогресс-сообщение, редактирует его по ходу выполнения
- Ветка `deep_task` в `handle_message()`
- `_user_network_preset: dict[int, str]` — per-user выбор пресета

### Новые команды
- `/network` — выбор пресета:
  ```
  [Анализ рынка]
  [Маркетинговая стратегия]
  [Глубокий ресёрч]
  ```

---

## Этап 7: Память + Документы + Голос

**Зачем:** персонализация через память, принимать файлы и голосовые.

### Новые файлы
- `utils/memory.py`:
  ```python
  class UserMemory(TypedDict):
      user_name: str
      preferences: list[str]
      past_tasks: list[str]   # последние 10

  def load_user_memory(user_id) -> UserMemory
  def save_user_memory(user_id, data) -> None
  def update_task_history(user_id, task_summary) -> None
  def get_memory_context(user_id) -> str  # форматирует в строку для system prompt
  ```

- `utils/media.py`:
  ```python
  async def transcribe_voice(file_path, ask_kwargs={}) -> str   # .ogg → Whisper API
  async def extract_document(file_path) -> str   # PDF/Excel/DOCX → текст (макс 8000 символов)
  async def describe_image(file_path, ask_kwargs={}) -> str    # base64 → vision model
  ```

### Изменения в существующих файлах

**`bot/main.py`**
- Инжектить `memory.get_memory_context()` в system prompt QA и deep_task агентов
- `MessageHandler(filters.VOICE)` → `handle_voice()` → транскрипция → обычный pipeline
- `MessageHandler(filters.Document.ALL)` → `handle_document()` → извлечение текста
- `MessageHandler(filters.PHOTO)` → `handle_photo()` → описание через vision
- После автоматизации: `memory.update_task_history()`
- Auto-save `update.effective_user.first_name` в память при первом сообщении

**`agents/qa.py`**
- Добавить `memory_context: str = ""` → препендит к system prompt

### Dependencies
```
PyPDF2>=3.0.0
openpyxl>=3.1.0
python-docx>=1.1.0
```

---

## Этап 8: RAG + Маркетплейс шаблонов + СНГ-интеграции

**Зачем:** поиск по загруженным документам, community-шаблоны, локальный рынок.

### Новые файлы
- `utils/rag.py` — ChromaDB per-user:
  ```python
  async def index_document(user_id, content, source_name) -> int  # кол-во чанков
  async def query(user_id, query_text, n_results=3) -> list[str]
  ```
  - Embeddings через `https://openrouter.ai/api/v1/embeddings`
  - Коллекция `f"user_{user_id}"` в `data/chroma/`

- `utils/marketplace.py`:
  ```python
  async def fetch_registry() -> list[TemplateEntry]  # GitHub registry.json, кэш 1 час
  async def fetch_template(url) -> dict
  def install_template(template_dict, user_id) -> str  # сохраняет в templates/
  ```

- `templates/cis/` — 6 новых шаблонов:
  - `bitrix24_lead_to_telegram.json`
  - `amocrm_deal_to_notification.json`
  - `wildberries_order_to_sheets.json`
  - `ozon_stock_alert.json`
  - `vk_post_to_telegram.json`
  - `hh_vacancy_monitor.json`

### Изменения в существующих файлах

**`agents/builder.py`**
- Расширить `TEMPLATE_METADATA`, `TEMPLATE_SIGNALS`, `PLACEHOLDER_MAPS` для 6 новых шаблонов
- `_load_template()` ищет в `templates/` и `templates/cis/`

**`agents/qa.py`**
- Добавить `rag_context: list[str] = []` → препендит релевантные чанки в user message

**`bot/main.py`**
- В `handle_document`: после извлечения текста → `rag.index_document()`
- В QA route: `rag.query()` → передать в `qa.answer(rag_context=...)`
- `/templates` команда → маркетплейс

**`config.py`**
- `TEMPLATE_REGISTRY_URL = os.getenv("TEMPLATE_REGISTRY_URL", "")`

### Dependencies
```
chromadb>=0.4.0
```

### Новые команды
- `/templates` — browse + install community-шаблонов

---

## Этап 9: MCP + Облачный деплой + Браузерные агенты

**Зачем:** мониторинг схем через агента, деплой n8n в облако без ручной настройки, автоматизация без API.

### Новые файлы
- `agents/mcp_n8n.py` — MCP сервер (отдельный процесс, `python -m agents.mcp_n8n`):
  - Tools: `check_workflow_status`, `fix_workflow` (LLM-assisted), `list_user_workflows`, `create_workflow_from_description`
  - Обёртывает `utils/n8n.py` через `mcp` SDK

- `utils/cloud.py`:
  ```python
  class DeployedService(TypedDict):
      service_id: str
      provider: str   # "railway" | "render"
      url: str
      status: str

  async def deploy_to_railway(config: ServiceConfig) -> DeployedService
  async def deploy_to_render(config: ServiceConfig) -> DeployedService
  async def get_service_status(service_id, provider) -> str
  ```

- `agents/browser_agent.py`:
  ```python
  async def run_browser_task(goal: str, ask_kwargs={}) -> BrowserTask
  # LLM планирует шаги (JSON action plan) → Playwright исполняет
  # Лимит: MAX_STEPS = 10
  ```

- `utils/job_queue.py` — asyncio background jobs:
  ```python
  async def submit(user_id, description, coro) -> str   # job_id
  def get_job(job_id) -> Job | None
  # Завершение → Telegram уведомление пользователю
  ```

### Изменения в существующих файлах

**`agents/router.py`**
- Добавить `"browser_task"` в `_VALID_TYPES`

**`bot/main.py`**
- `_handle_browser_task()` → `job_queue.submit()`
- Новые команды: `/deploy`, `/jobs`, `/mcp`

**`utils/n8n.py`**
- Добавить `get_workflow(workflow_id)`, `deactivate(workflow_id)`, `list_workflows()`

### Dependencies
```
mcp>=1.0.0
playwright>=1.44.0
# playwright install chromium — отдельно в Dockerfile
```

### Новые команды
- `/deploy` — мастер облачного деплоя n8n (Railway/Render)
- `/jobs` — список фоновых задач пользователя
- `/mcp` — инструкция для подключения MCP-сервера

---

## Итоговый роадмап

```
✅ Этап 1   Бот + QA Agent
✅ Этап 2   Router + Interpreter
✅ Этап 3   Templates + Builder + n8n
   Этап 4   Claude API + /model (выбор модели через кнопки)
   Этап 5   Billing (OpenRouter ключи, подписка, on-demand, fallback, /services)
   Этап 6   Agent Networks (/network: market analysis, marketing, research)
   Этап 7   Память + Документы (PDF/Excel) + Голосовые сообщения
   Этап 8   RAG + Маркетплейс шаблонов + СНГ-интеграции
   Этап 9   MCP + Облачный деплой + Браузерные агенты + Long-running tasks
```

## Сводная таблица файлов

| Этап | Новые файлы | Изменяемые файлы |
|---|---|---|
| 4 | `utils/model_config.py` | `utils/llm.py`, `bot/main.py`, все 4 агента |
| 5 | `utils/billing.py`, `utils/workflows_db.py`, `utils/payments.py` | `config.py`, `bot/main.py`, `utils/llm.py`, `utils/n8n.py` |
| 6 | `agents/network.py` | `agents/router.py`, `bot/main.py` |
| 7 | `utils/memory.py`, `utils/media.py` | `bot/main.py`, `agents/qa.py` |
| 8 | `utils/rag.py`, `utils/marketplace.py`, `templates/cis/*.json` x6 | `agents/builder.py`, `agents/qa.py`, `bot/main.py`, `config.py` |
| 9 | `agents/mcp_n8n.py`, `agents/browser_agent.py`, `utils/cloud.py`, `utils/job_queue.py` | `agents/router.py`, `bot/main.py`, `utils/n8n.py` |

## Ключевой инвариант

`utils/llm.py::ask()` — единственная точка LLM-вызовов. Вся логика выбора модели, провайдера и ключа живёт только там. Никогда не импортировать OpenAI/Anthropic SDK напрямую из агентов.
