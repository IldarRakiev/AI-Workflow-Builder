import logging
from typing import TypedDict

from utils.llm import ask, parse_json_response

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6

SYSTEM_PROMPT = """\
You are a message classifier for an AI automation system. Your only job is to classify the user's message into one of three types.

Types:
- "qa": user wants information, an explanation, or an answer to a question
- "automation": user wants to set up a recurring process, workflow, or trigger-action rule (imperative verbs like "save", "send", "notify", "when X do Y")
- "hybrid": user explicitly wants BOTH an explanation AND to set up a workflow in the same message
- "deep_task": user asks for a complex multi-step analysis, research, strategy, brainstorm, or content creation that benefits from multiple perspectives (keywords: "проанализируй", "исследуй", "разработай стратегию", "придумай контент-план", "мозговой штурм", "совет с разных сторон", "глубокий анализ")

Rules:
- If confidence is below 0.6, default to "qa"
- Consider the conversation history for context
- Use "deep_task" only for complex, multi-faceted requests — simple questions are "qa"

Examples:
User: "как подключить Google Sheets к Telegram?" → {"type": "qa", "confidence": 0.95, "intent": "User asks how to connect Google Sheets to Telegram"}
User: "сохраняй каждое сообщение из группы в таблицу" → {"type": "automation", "confidence": 0.97, "intent": "Save Telegram group messages to a spreadsheet"}
User: "объясни как работает webhook и настрой его для меня" → {"type": "hybrid", "confidence": 0.88, "intent": "Explain webhook and set one up"}
User: "проанализируй рынок доставки еды в Москве" → {"type": "deep_task", "confidence": 0.95, "intent": "Deep market analysis of food delivery in Moscow"}
User: "помоги придумать контент-план для инстаграма кофейни" → {"type": "deep_task", "confidence": 0.92, "intent": "Content strategy for a coffee shop Instagram"}
User: "мне нужен совет с разных точек зрения — стоит ли менять работу" → {"type": "deep_task", "confidence": 0.90, "intent": "Multi-perspective advice on career change"}

Respond with ONLY a JSON object in this exact format:
{"type": "qa", "confidence": 0.95, "intent": "short description of what the user wants"}

No other text. No markdown. Just the JSON object.\
"""


class RouteResult(TypedDict):
    type: str          # "qa" | "automation" | "hybrid"
    confidence: float  # 0.0–1.0
    intent: str        # short description


_VALID_TYPES = {"qa", "automation", "hybrid", "deep_task"}
_FALLBACK: RouteResult = {"type": "qa", "confidence": 0.0, "intent": "classification failed"}


async def classify(user_message: str, history: list[dict] | None = None, ask_kwargs: dict = {}) -> RouteResult:
    """Classify user intent into qa / automation / hybrid."""
    # Use last 4 history entries (2 exchanges) — enough context for a cheap call
    recent = list(history[-4:]) if history else []
    recent.append({"role": "user", "content": user_message})

    try:
        raw = await ask(recent, system=SYSTEM_PROMPT, **ask_kwargs)
        data = parse_json_response(raw)

        route_type = data.get("type", "qa")
        confidence = float(data.get("confidence", 0.0))
        intent = data.get("intent", "")

        # Apply fallback rules
        if route_type not in _VALID_TYPES or confidence < CONFIDENCE_THRESHOLD:
            route_type = "qa"

        result: RouteResult = {"type": route_type, "confidence": confidence, "intent": intent}
        return result

    except Exception as e:
        logger.error("Router classification failed: %s", e)
        return _FALLBACK
