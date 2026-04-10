import json
import logging
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "memory"

MAX_PAST_TASKS = 10


class UserMemory(TypedDict):
    user_name: str
    preferences: list[str]
    past_tasks: list[str]


_DEFAULT_MEMORY: UserMemory = {
    "user_name": "",
    "preferences": [],
    "past_tasks": [],
}


def _user_file(user_id: int) -> Path:
    return DATA_DIR / f"{user_id}.json"


def load_user_memory(user_id: int) -> UserMemory:
    path = _user_file(user_id)
    if not path.exists():
        return {**_DEFAULT_MEMORY}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "user_name": data.get("user_name", ""),
            "preferences": data.get("preferences", []),
            "past_tasks": data.get("past_tasks", []),
        }
    except Exception as e:
        logger.warning("Failed to load memory for user %s: %s", user_id, e)
        return {**_DEFAULT_MEMORY}


def save_user_memory(user_id: int, data: UserMemory) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _user_file(user_id)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to save memory for user %s: %s", user_id, e)


def update_task_history(user_id: int, task_summary: str) -> None:
    mem = load_user_memory(user_id)
    mem["past_tasks"].append(task_summary)
    if len(mem["past_tasks"]) > MAX_PAST_TASKS:
        mem["past_tasks"] = mem["past_tasks"][-MAX_PAST_TASKS:]
    save_user_memory(user_id, mem)


def ensure_user_name(user_id: int, name: str) -> None:
    """Set user_name if not already saved."""
    if not name:
        return
    mem = load_user_memory(user_id)
    if not mem["user_name"]:
        mem["user_name"] = name
        save_user_memory(user_id, mem)
        logger.info("Saved name '%s' for user %s", name, user_id)


def get_memory_context(user_id: int) -> str:
    """Format user memory as a string to prepend to system prompts."""
    mem = load_user_memory(user_id)
    parts: list[str] = []

    if mem["user_name"]:
        parts.append(f"User's name: {mem['user_name']}")

    if mem["preferences"]:
        parts.append("Preferences: " + "; ".join(mem["preferences"]))

    if mem["past_tasks"]:
        recent = mem["past_tasks"][-5:]
        parts.append("Recent tasks: " + "; ".join(recent))

    if not parts:
        return ""

    return "Known info about the user:\n" + "\n".join(parts)
