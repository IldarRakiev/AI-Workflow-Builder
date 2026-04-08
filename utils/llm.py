import json
import logging
from config import LLM_PROVIDER, LLM_MODEL

logger = logging.getLogger(__name__)


def parse_json_response(raw: str) -> dict:
    """Parse a JSON object from an LLM response, handling markdown fences and prose."""
    text = raw.strip()

    # Strip markdown code fences if present
    if "```" in text:
        parts = text.split("```")
        # parts[1] is the content inside the first fence pair
        if len(parts) >= 3:
            text = parts[1]
            # Remove optional language tag (e.g. "json\n{...")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

    # First attempt: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: extract first {...} block from raw string
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response: {raw[:200]!r}")


async def ask(
    messages: list[dict],
    system: str = "",
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
) -> str:
    """Send messages to the configured LLM and return the assistant's text reply.

    model_override / provider_override let callers switch model per-request
    without touching global config (used by per-user model selection).
    """
    provider = provider_override or LLM_PROVIDER
    model = model_override or LLM_MODEL

    if provider == "openrouter":
        return await _ask_openrouter(messages, system, model=model)
    elif provider == "anthropic":
        return await _ask_anthropic(messages, system, model=model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}")


async def _ask_openrouter(messages: list[dict], system: str, *, model: str) -> str:
    from openai import AsyncOpenAI
    from config import OPENROUTER_API_KEY

    client = AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    try:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=full_messages,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("OpenRouter request failed: %s", e)
        raise


async def _ask_anthropic(messages: list[dict], system: str, *, model: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic SDK is not installed. Run: pip install anthropic"
        )

    from config import ANTHROPIC_API_KEY

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    kwargs = {
        "model": model,
        "max_tokens": 1024,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    try:
        response = await client.messages.create(**kwargs)
        return response.content[0].text
    except Exception as e:
        logger.error("Anthropic request failed: %s", e)
        raise