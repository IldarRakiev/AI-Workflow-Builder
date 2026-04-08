import json
import logging
import re
from pathlib import Path
from typing import TypedDict

from agents.interpreter import TaskStructure
from config import TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY, ANTHROPIC_API_KEY, LLM_MODEL, LLM_PROVIDER
from utils.llm import ask, parse_json_response

logger = logging.getLogger(__name__)

# Auto-fill: placeholders resolved from project .env (user doesn't need to provide these)
_ENV_AUTOFILL: dict[str, str | None] = {
    "BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "AI_API_URL": (
        "https://openrouter.ai/api/v1/chat/completions" if LLM_PROVIDER == "openrouter"
        else "https://api.anthropic.com/v1/messages"
    ),
    "AI_API_KEY": OPENROUTER_API_KEY if LLM_PROVIDER == "openrouter" else ANTHROPIC_API_KEY,
    "AI_MODEL": LLM_MODEL,
}

# User-friendly guides for each placeholder that requires manual input
PLACEHOLDER_GUIDES: dict[str, str] = {
    "CHAT_ID": (
        "Как узнать Chat ID:\n"
        "  1. Напиши боту @userinfobot в Telegram\n"
        "  2. Он ответит твоим числовым Chat ID\n"
        "  3. Для группы: добавь @userinfobot в группу"
    ),
    "SHEET_ID": (
        "Как найти Sheet ID:\n"
        "  Открой таблицу в Google Sheets.\n"
        "  В URL: docs.google.com/spreadsheets/d/<ВОТ_ЭТОТ_ID>/edit\n"
        "  Скопируй длинную строку между /d/ и /edit"
    ),
    "SHEET_NAME": (
        "Название листа в Google Sheets.\n"
        "  По умолчанию: «Лист1» (или «Sheet1» для англ.)"
    ),
    "WEBHOOK_PATH": (
        "Произвольный путь для webhook (латиницей).\n"
        "  Например: my-form-webhook"
    ),
}

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Human-readable metadata for each template (used in LLM selection prompt)
TEMPLATE_METADATA = {
    "telegram_to_sheets": "Saves Telegram messages to Google Sheets",
    "telegram_to_ai_reply": "Telegram bot that replies to messages using an AI model",
    "form_to_notification": "Sends a Telegram notification when a form/webhook is submitted",
}

# Keywords for fast (no-LLM) template selection
TEMPLATE_SIGNALS: dict[str, dict[str, list[str]]] = {
    "telegram_to_sheets": {
        "trigger": ["telegram", "сообщен", "группа", "канал", "чат"],
        "destination": ["sheets", "таблиц", "google", "spreadsheet", "гугл"],
    },
    "telegram_to_ai_reply": {
        "trigger": ["telegram", "сообщен", "группа", "чат"],
        "destination": ["ответ", "reply", "ai", "gpt", "claude", "бот", "автоответ"],
    },
    "form_to_notification": {
        "trigger": ["форм", "form", "webhook", "заявк", "submit"],
        "destination": ["уведомлен", "notification", "telegram", "оповещ"],
    },
}

# Maps placeholder names → entity keys extracted by Interpreter
PLACEHOLDER_MAPS: dict[str, dict[str, str]] = {
    "telegram_to_sheets": {
        "BOT_TOKEN": "bot_token",
        "SHEET_ID": "sheet_id",
        "SHEET_NAME": "sheet_name",
    },
    "telegram_to_ai_reply": {
        "BOT_TOKEN": "bot_token",
        "AI_API_URL": "ai_api_url",
        "AI_API_KEY": "ai_api_key",
        "AI_MODEL": "ai_model",
        "CHAT_ID": "chat_id",
    },
    "form_to_notification": {
        "WEBHOOK_PATH": "webhook_path",
        "BOT_TOKEN": "bot_token",
        "CHAT_ID": "chat_id",
    },
}

SYSTEM_PROMPT_SELECT = """\
You are an automation template selector. Given a task structure, choose the best matching template.

Available templates:
- "telegram_to_sheets": Saves Telegram messages to Google Sheets
- "telegram_to_ai_reply": Telegram bot that replies to messages using an AI model
- "form_to_notification": Sends a Telegram notification when a form/webhook is submitted
- "none": No template matches — a custom workflow is needed

Respond with ONLY a JSON object:
{"template_id": "telegram_to_sheets", "reason": "brief reason"}\
"""

SYSTEM_PROMPT_FILL = """\
You are extracting values for automation workflow placeholders.
Given a task description and extracted entities, determine the value for each placeholder.
If a value cannot be determined from the context, return the string "PENDING".

Respond with ONLY a JSON object mapping placeholder names to values:
{"PLACEHOLDER_NAME": "value_or_PENDING", ...}\
"""


class BuilderResult(TypedDict):
    template_id: str
    workflow_json: dict
    filled_placeholders: dict
    summary: str


_FALLBACK: BuilderResult = {
    "template_id": "error",
    "workflow_json": {},
    "filled_placeholders": {},
    "summary": "Не удалось сгенерировать автоматизацию. Попробуй описать задачу подробнее.",
}


async def generate(task: TaskStructure, user_message: str = "", ask_kwargs: dict = {}) -> BuilderResult:
    """Select a template and fill it with task data. Returns BuilderResult."""
    try:
        template_id = _score_templates(task)
        if template_id is None:
            template_id = await _select_template_llm(task, ask_kwargs=ask_kwargs)

        if template_id == "none" or template_id not in TEMPLATE_METADATA:
            workflow_json = _build_custom_stub(task)
            return {
                "template_id": "custom",
                "workflow_json": workflow_json,
                "filled_placeholders": {},
                "summary": (
                    "Готового шаблона для этой задачи нет. "
                    "Создал базовую заготовку — можешь доработать её в n8n."
                ),
            }

        logger.info("Builder selected template: %s", template_id)

        template = _load_template(template_id)
        template_str = json.dumps(template, ensure_ascii=False)
        placeholders = _find_placeholders(template_str)

        # Rule-based filling from entities
        fills = _fill_rule_based(placeholders, task, template_id)

        # LLM filling for remaining placeholders
        remaining = [p for p in placeholders if p not in fills]
        if remaining:
            llm_fills = await _fill_llm(remaining, task, user_message, ask_kwargs=ask_kwargs)
            fills.update(llm_fills)

        # Mark still-unfilled as PENDING
        for p in placeholders:
            if p not in fills or not fills[p] or fills[p] == "PENDING":
                fills[p] = f"PENDING_{p}"

        workflow_json = _apply_fills(template_str, fills)
        summary = _build_summary(template_id, fills, task)

        return {
            "template_id": template_id,
            "workflow_json": workflow_json,
            "filled_placeholders": fills,
            "summary": summary,
        }

    except Exception as e:
        logger.error("Builder.generate failed: %s", e)
        return _FALLBACK


def _score_templates(task: TaskStructure) -> str | None:
    """Return template_id if one clearly wins on keyword scoring, else None."""
    search_text = (
        f"{task['trigger']} {task['destination']} "
        + " ".join(str(v) for v in task["entities"].values())
    ).lower()

    scores: dict[str, int] = {}
    for tid, signals in TEMPLATE_SIGNALS.items():
        score = sum(1 for kw in signals["trigger"] if kw in search_text)
        score += sum(1 for kw in signals["destination"] if kw in search_text)
        scores[tid] = score

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_id, best_score = sorted_scores[0]
    second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0

    if best_score > 0 and best_score >= second_score + 2:
        return best_id
    return None


async def _select_template_llm(task: TaskStructure, ask_kwargs: dict = {}) -> str:
    """Ask LLM to select the best template. Returns template_id or 'none'."""
    messages = [{"role": "user", "content": json.dumps({"task": task}, ensure_ascii=False)}]
    try:
        raw = await ask(messages, system=SYSTEM_PROMPT_SELECT, **ask_kwargs)
        data = parse_json_response(raw)
        chosen = data.get("template_id", "none")
        if chosen not in {*TEMPLATE_METADATA.keys(), "none"}:
            return "none"
        return chosen
    except Exception as e:
        logger.error("LLM template selection failed: %s", e)
        return "none"


def _load_template(template_id: str) -> dict:
    path = TEMPLATES_DIR / f"{template_id}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_placeholders(template_str: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\{\{(\w+)\}\}", template_str)))


def _fill_rule_based(placeholders: list[str], task: TaskStructure, template_id: str) -> dict[str, str]:
    fills: dict[str, str] = {}
    mapping = PLACEHOLDER_MAPS.get(template_id, {})
    for placeholder in placeholders:
        # Priority 1: auto-fill from project .env
        env_value = _ENV_AUTOFILL.get(placeholder)
        if env_value:
            fills[placeholder] = env_value
            continue
        # Priority 2: fill from interpreter-extracted entities
        entity_key = mapping.get(placeholder)
        if entity_key and entity_key in task["entities"]:
            value = str(task["entities"][entity_key])
            if value and value.lower() != "pending":
                fills[placeholder] = value
    return fills


async def _fill_llm(remaining: list[str], task: TaskStructure, user_message: str, ask_kwargs: dict = {}) -> dict[str, str]:
    """Ask LLM to fill in remaining placeholders from context."""
    prompt = (
        f"Task description: {user_message}\n\n"
        f"Extracted task structure: {json.dumps(task, ensure_ascii=False)}\n\n"
        f"Placeholders to fill: {remaining}"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        raw = await ask(messages, system=SYSTEM_PROMPT_FILL, **ask_kwargs)
        data = parse_json_response(raw)
        return {k: v for k, v in data.items() if k in remaining and v and v != "PENDING"}
    except Exception as e:
        logger.error("LLM placeholder fill failed: %s", e)
        return {}


def _json_escape(value: str) -> str:
    """Escape a string for safe substitution inside a JSON string value."""
    return json.dumps(value)[1:-1]  # strips surrounding quotes


def _apply_fills(template_str: str, fills: dict[str, str]) -> dict:
    """Replace all {{PLACEHOLDER}} in template_str and return parsed dict."""
    result = template_str
    for placeholder, value in fills.items():
        result = result.replace(f"{{{{{placeholder}}}}}", _json_escape(value))
    return json.loads(result)


def _build_summary(template_id: str, fills: dict[str, str], task: TaskStructure) -> str:
    pending = [k for k, v in fills.items() if v.startswith("PENDING_")]
    names = {
        "telegram_to_sheets": "Telegram → Google Sheets",
        "telegram_to_ai_reply": "Telegram → AI ответ",
        "form_to_notification": "Форма → Telegram уведомление",
    }
    name = names.get(template_id, template_id)

    if not pending:
        return f"Отлично! Подготовил автоматизацию «{name}». Все данные заполнены."
    else:
        return (
            f"Подготовил автоматизацию «{name}».\n"
            f"Триггер: {task['trigger']}.\n"
            f"Действия: {', '.join(task['actions']) if task['actions'] else '—'}."
        )


def get_pending_guides(fills: dict[str, str]) -> str:
    """Build user-friendly help text for all PENDING placeholders."""
    pending = [k for k, v in fills.items() if v.startswith("PENDING_")]
    if not pending:
        return ""
    parts = ["Нужно заполнить:"]
    for name in pending:
        guide = PLACEHOLDER_GUIDES.get(name)
        if guide:
            parts.append(f"\n{guide}")
        else:
            parts.append(f"\n{name} — значение нужно указать вручную")
    return "\n".join(parts)


def _build_custom_stub(task: TaskStructure) -> dict:
    """Return a minimal valid n8n workflow when no template matches."""
    return {
        "name": task["trigger"][:50] or "Custom Workflow",
        "nodes": [
            {
                "id": "custom-0000-0000-0000-000000000001",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [250, 300],
                "webhookId": "custom-0000-0000-0000-000000000099",
                "parameters": {
                    "httpMethod": "POST",
                    "path": "custom-webhook",
                    "responseMode": "onReceived",
                },
            },
            {
                "id": "custom-0000-0000-0000-000000000002",
                "name": "No Operation",
                "type": "n8n-nodes-base.noOp",
                "typeVersion": 1,
                "position": [500, 300],
                "parameters": {},
            },
        ],
        "connections": {
            "Webhook": {
                "main": [[{"node": "No Operation", "type": "main", "index": 0}]]
            }
        },
        "settings": {"executionOrder": "v1"},
        "meta": {"templateId": "custom"},
    }
