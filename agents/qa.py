from utils.llm import ask

SYSTEM_PROMPT = (
    "Ты — помощник системы AI Automation Builder. "
    "Отвечай точно, кратко и по делу. "
    "Если вопрос касается автоматизации задач — дай практичный ответ. "
    "Отвечай на том языке, на котором написан вопрос."
)


async def answer(
    user_message: str,
    history: list[dict] = None,
    ask_kwargs: dict = {},
    memory_context: str = "",
) -> str:
    """Return an LLM answer, optionally enriched with user memory context."""
    system = SYSTEM_PROMPT
    if memory_context:
        system = f"{memory_context}\n\n{SYSTEM_PROMPT}"

    messages = list(history) if history else []
    messages.append({"role": "user", "content": user_message})
    return await ask(messages, system=system, **ask_kwargs)