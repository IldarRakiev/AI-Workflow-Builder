import json
import logging
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
USER_MODELS_FILE = DATA_DIR / "user_models.json"

AVAILABLE_MODELS: list[dict[str, str]] = [
    # OpenAI via OpenRouter
    {"id": "openrouter:openai/gpt-4o-mini", "provider": "openrouter", "model": "openai/gpt-4o-mini", "label": "GPT-4o mini"},
    {"id": "openrouter:openai/gpt-4o", "provider": "openrouter", "model": "openai/gpt-4o", "label": "GPT-4o"},
    {"id": "openrouter:openai/gpt-5", "provider": "openrouter", "model": "openai/gpt-5", "label": "GPT-5"},
    {"id": "openrouter:openai/gpt-5.4", "provider": "openrouter", "model": "openai/gpt-5.4", "label": "GPT-5.4"},
    # Anthropic via OpenRouter
    {"id": "openrouter:anthropic/claude-sonnet-4.5", "provider": "openrouter", "model": "anthropic/claude-sonnet-4.5", "label": "Claude Sonnet 4.5"},
    {"id": "openrouter:anthropic/claude-sonnet-4.6", "provider": "openrouter", "model": "anthropic/claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
    {"id": "openrouter:anthropic/claude-opus-4.5", "provider": "openrouter", "model": "anthropic/claude-opus-4.5", "label": "Claude Opus 4.5"},
    {"id": "openrouter:anthropic/claude-opus-4.6", "provider": "openrouter", "model": "anthropic/claude-opus-4.6", "label": "Claude Opus 4.6"},
]


class ModelConfig(TypedDict):
    provider: str
    model: str


DEFAULT_CONFIG: ModelConfig = {"provider": "openrouter", "model": "openai/gpt-4o-mini"}


def _load_db() -> dict[str, ModelConfig]:
    if not USER_MODELS_FILE.exists():
        return {}
    try:
        return json.loads(USER_MODELS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load user_models.json: %s", e)
        return {}


def _save_db(db: dict[str, ModelConfig]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_MODELS_FILE.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_user_model(user_id: int) -> ModelConfig:
    db = _load_db()
    return db.get(str(user_id), DEFAULT_CONFIG)


def set_user_model(user_id: int, provider: str, model: str) -> None:
    db = _load_db()
    db[str(user_id)] = {"provider": provider, "model": model}
    _save_db(db)
