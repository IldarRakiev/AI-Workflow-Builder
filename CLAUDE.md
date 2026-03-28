# AI Automation Builder — CLAUDE.md

## Что это за проект

Система из двух модулей:
1. **Automation Builder** — пользователь описывает задачу на естественном языке → система отвечает (QA) или генерирует автоматизацию (workflow в n8n)
2. **Marketing Engine** — внутренний инструмент для генерации и оптимизации маркетинга продукта

Интерфейс: Telegram бот. Никаких dashboard, форм, UI — только чат.

---

## Стек

| Слой | Технология |
|---|---|
| Interface | Telegram Bot (python-telegram-bot) |
| Backend | Python 3.11+ |
| LLM (тест) | OpenAI GPT-4o-mini через OpenRouter |
| LLM (прод) | Anthropic Claude Sonnet / Opus (API) |
| Automation | n8n (self-hosted или cloud) |
| Env | .env через python-dotenv |

---

## Архитектура агентов

```
User input (Telegram)
    │
    ▼
Router Agent          ← классифицирует: qa / automation / hybrid
    │
    ├── QA Agent      ← отвечает на вопросы
    │
    └── Interpreter Agent   ← извлекает структуру задачи
            │
            ▼
        Builder Agent       ← выбирает шаблон, генерирует workflow
            │
            ▼
        n8n Integration     ← создаёт и активирует workflow
```

---

## Структура проекта

```
AI-Workflow-Builder/
├── CLAUDE.md
├── .env.example
├── requirements.txt
├── config.py               # настройки, константы
├── bot/
│   └── main.py             # точка входа Telegram бота
├── agents/
│   ├── router.py           # Router Agent
│   ├── qa.py               # QA Agent
│   ├── interpreter.py      # Interpreter Agent
│   └── builder.py          # Builder Agent
├── templates/
│   └── *.json              # n8n workflow templates
├── marketing/
│   ├── ideas.py            # Ideas Agent
│   ├── content.py          # Content Agent
│   ├── feedback.py         # Feedback Agent
│   └── optimizer.py        # Optimization Agent
└── utils/
    ├── llm.py              # обёртка над LLM API (OpenRouter / Anthropic)
    └── n8n.py              # клиент для n8n API
```

---

## Переменные окружения

```env
TELEGRAM_BOT_TOKEN=
OPENROUTER_API_KEY=        # для тестирования (GPT-4o-mini)
ANTHROPIC_API_KEY=         # для прода (Claude Sonnet/Opus)
LLM_PROVIDER=openrouter    # openrouter | anthropic
N8N_BASE_URL=
N8N_API_KEY=
```

---

## Этапы разработки

### Этап 1 — Telegram бот + QA
- [ ] Структура проекта, .env, requirements
- [ ] Telegram бот (echo + базовая обработка)
- [ ] LLM обёртка (OpenRouter)
- [ ] QA Agent (отвечает на любые вопросы)

### Этап 2 — Router + Interpreter
- [ ] Router Agent (qa / automation / hybrid)
- [ ] Interpreter Agent (извлечение структуры задачи)
- [ ] Тесты классификации

### Этап 3 — Templates + Builder
- [ ] 3 шаблона: telegram→sheets, telegram→AI reply, form→notification
- [ ] Builder Agent (выбор шаблона + генерация workflow)
- [ ] n8n интеграция (создание + активация workflow)

### Этап 4 — Marketing Engine
- [ ] Ideas Agent
- [ ] Content Agent
- [ ] Feedback + Optimization loop

### Этап 5 — Переход на Claude API
- [ ] Заменить OpenRouter на Anthropic SDK
- [ ] Использовать Sonnet для агентов, Opus для сложных задач
- [ ] Оптимизация промптов

---

## Соглашения

- Все агенты — отдельные модули в `agents/`
- LLM вызовы только через `utils/llm.py` — не вызывать API напрямую из агентов
- Промпты хранятся внутри агентов как константы (не в отдельных файлах)
- Логирование через стандартный `logging`, не `print`
- Типизация через `dataclasses` или `TypedDict` для структур данных агентов

---

## Контекст для Claude Code

- Проект на ранней стадии, MVP
- Приоритет: работающий прототип, не идеальный код
- Не добавлять лишнюю сложность — минимум нужный для текущего этапа
- Переключение LLM провайдера должно быть через одну переменную в .env
