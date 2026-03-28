from utils.llm import ask

SYSTEM_PROMPT = (
    "Ты — помощник системы AI Automation Builder. "
    "Отвечай точно, кратко и по делу. "
    "Если вопрос касается автоматизации задач — дай практичный ответ. "
    "Отвечай на том языке, на котором написан вопрос."
)


async def answer(user_message: str, history: list[dict] = None) -> str:
    """Return an LLM answer for user_message, optionally using prior history."""
    messages = list(history) if history else []
    messages.append({"role": "user", "content": user_message})
    return await ask(messages, system=SYSTEM_PROMPT)