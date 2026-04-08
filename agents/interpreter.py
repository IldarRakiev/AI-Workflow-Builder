import logging
from typing import TypedDict

from utils.llm import ask, parse_json_response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an automation workflow analyst. Extract the structure of the user's automation task.

Fields to extract:
- trigger: what event starts the automation (e.g. "new Telegram message", "every day at 9:00", "form submitted")
- actions: ordered list of steps to perform
- destination: where the final result goes (e.g. "Google Sheets", "Telegram", "Email") — use "unknown" if unclear
- entities: flat dict of any specific names, URLs, times, service names found in the message
- summary: a friendly confirmation in the SAME LANGUAGE as the user's message

Example input: "сохраняй каждое новое сообщение из Telegram-группы в Google Sheets"
Example output:
{
  "trigger": "новое сообщение в Telegram-группе",
  "actions": ["получить сообщение", "сохранить строку в Google Sheets"],
  "destination": "Google Sheets",
  "entities": {"service": "Telegram", "destination_service": "Google Sheets"},
  "summary": "Понял! Буду сохранять каждое новое сообщение из Telegram-группы в Google Sheets."
}

Respond with ONLY a JSON object matching this exact schema. No other text. No markdown.\
"""


class TaskStructure(TypedDict):
    trigger: str
    actions: list[str]
    destination: str
    entities: dict


class InterpreterResult(TypedDict):
    task: TaskStructure
    summary: str


_FALLBACK_SUMMARY = "Не удалось разобрать задачу, попробуй описать подробнее."


async def extract(user_message: str, history: list[dict] | None = None, ask_kwargs: dict = {}) -> InterpreterResult:
    """Extract automation task structure from user message."""
    # Use last 6 history entries (3 exchanges) — automation intent often spans turns
    recent = list(history[-6:]) if history else []
    recent.append({"role": "user", "content": user_message})

    try:
        raw = await ask(recent, system=SYSTEM_PROMPT, **ask_kwargs)
        data = parse_json_response(raw)

        task: TaskStructure = {
            "trigger": data.get("trigger", "unknown"),
            "actions": data.get("actions", []),
            "destination": data.get("destination", "unknown"),
            "entities": data.get("entities", {}),
        }

        summary = data.get("summary", "").strip()
        if not summary:
            actions_str = ", ".join(task["actions"]) if task["actions"] else "—"
            summary = f"Понял! Триггер: {task['trigger']}. Действия: {actions_str}."

        return {"task": task, "summary": summary}

    except Exception as e:
        logger.error("Interpreter extraction failed: %s", e)
        fallback_task: TaskStructure = {
            "trigger": "unknown",
            "actions": [],
            "destination": "unknown",
            "entities": {},
        }
        return {"task": fallback_task, "summary": _FALLBACK_SUMMARY}
