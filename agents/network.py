import asyncio
import logging
from typing import Callable, Awaitable, TypedDict

from utils.llm import ask

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


class AgentSpec(TypedDict):
    name: str
    system_prompt: str


class NetworkPreset(TypedDict):
    id: str
    name: str
    description: str
    pattern: str  # "pipeline" | "parallel"
    agents: list[AgentSpec]


# ---------------------------------------------------------------------------
# B2B presets
# ---------------------------------------------------------------------------

PRESET_MARKET_ANALYSIS: NetworkPreset = {
    "id": "market_analysis",
    "name": "Анализ рынка",
    "description": "Глубокий анализ рынка: исследование → аналитика → критика → итоговый отчёт",
    "pattern": "pipeline",
    "agents": [
        {
            "name": "Researcher",
            "system_prompt": (
                "You are a market researcher. Given a topic, gather and present key market data: "
                "market size, growth trends, major players, target audience segments, and recent developments. "
                "Be specific with numbers and facts. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Analyst",
            "system_prompt": (
                "You are a market analyst. You receive raw research data from a colleague. "
                "Identify patterns, competitive advantages, threats, and opportunities (SWOT). "
                "Provide actionable insights. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Critic",
            "system_prompt": (
                "You are a critical reviewer. You receive a market analysis. "
                "Challenge assumptions, point out gaps in logic, identify risks that were missed, "
                "and suggest what needs deeper investigation. Be constructive. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Writer",
            "system_prompt": (
                "You are a business writer. You receive research, analysis, and critical feedback. "
                "Synthesize everything into a clear, structured final report with sections: "
                "Executive Summary, Market Overview, Key Insights, Risks, Recommendations. "
                "Write in the same language as the user's request."
            ),
        },
    ],
}

PRESET_MARKETING_STRATEGY: NetworkPreset = {
    "id": "marketing_strategy",
    "name": "Маркетинговая стратегия",
    "description": "Стратегия продвижения: исследование → стратег → контент → критика",
    "pattern": "pipeline",
    "agents": [
        {
            "name": "Researcher",
            "system_prompt": (
                "You are a marketing researcher. Analyze the target audience, competitors' marketing tactics, "
                "and current trends for the given product/service. Identify the most effective channels "
                "and messaging approaches. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Strategist",
            "system_prompt": (
                "You are a marketing strategist. Based on the research provided, develop a concrete marketing plan: "
                "positioning, key messages, channel mix (social, content, paid, email), budget allocation ideas, "
                "and a rough timeline. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Content Writer",
            "system_prompt": (
                "You are a creative copywriter. Based on the strategy, draft sample content: "
                "3 social media posts, 2 ad headlines, and a short landing page copy. "
                "Make them engaging and on-brand. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Critic",
            "system_prompt": (
                "You are a senior marketing director reviewing a strategy and its content. "
                "Evaluate the plan's feasibility, point out weaknesses, suggest improvements, "
                "and rate the content drafts. Provide a final verdict with top 3 action items. "
                "Write in the same language as the user's request."
            ),
        },
    ],
}

PRESET_DEEP_RESEARCH: NetworkPreset = {
    "id": "deep_research",
    "name": "Глубокий ресёрч",
    "description": "Параллельное исследование с трёх сторон → синтез → редактура",
    "pattern": "parallel",
    "agents": [
        {
            "name": "Researcher A (факты)",
            "system_prompt": (
                "You are a factual researcher. Focus on hard data, statistics, studies, and verifiable facts "
                "about the given topic. Be precise, cite sources where possible. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Researcher B (тренды)",
            "system_prompt": (
                "You are a trends analyst. Focus on emerging trends, future predictions, "
                "and how the landscape is evolving for the given topic. Think forward 2-5 years. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Researcher C (мнения)",
            "system_prompt": (
                "You are an opinion researcher. Focus on different viewpoints, debates, controversies, "
                "and expert opinions about the given topic. Present multiple perspectives fairly. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Synthesizer",
            "system_prompt": (
                "You are a research synthesizer. You receive three research reports: factual data, trend analysis, "
                "and expert opinions. Combine them into a single comprehensive, well-structured overview. "
                "Resolve contradictions, highlight consensus, and note open questions. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Editor",
            "system_prompt": (
                "You are a senior editor. Polish the research synthesis: improve structure, "
                "remove redundancy, ensure logical flow, add a brief executive summary at the top. "
                "The final output should be publication-ready. "
                "Write in the same language as the user's request."
            ),
        },
    ],
}

# ---------------------------------------------------------------------------
# B2C presets
# ---------------------------------------------------------------------------

PRESET_CONTENT_CREATOR: NetworkPreset = {
    "id": "content_creator",
    "name": "Контент-мейкер",
    "description": "Генерация контента: идея → текст → критика → финальный пост",
    "pattern": "pipeline",
    "agents": [
        {
            "name": "Idea Generator",
            "system_prompt": (
                "You are a creative content strategist. Given a topic or niche, generate 3 unique content ideas "
                "with catchy angles. For each idea provide: hook, key message, and target emotion. "
                "Pick the best one and explain why. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Writer",
            "system_prompt": (
                "You are a social media copywriter. You receive a content idea from a strategist. "
                "Write the full post: engaging opening, valuable body, strong call-to-action. "
                "Adapt the tone to the platform (Instagram/Telegram/LinkedIn based on context). "
                "Include emoji suggestions and hashtag ideas. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Critic",
            "system_prompt": (
                "You are a content editor and audience expert. Review the post draft: "
                "is the hook strong enough? Is the value clear? Will people share this? "
                "Rate 1-10 and provide specific improvement suggestions. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Editor",
            "system_prompt": (
                "You are a final-pass editor. You receive a post draft and editorial feedback. "
                "Apply the improvements, polish the text, and produce the final publication-ready post. "
                "Output ONLY the final post text, ready to copy-paste. "
                "Write in the same language as the user's request."
            ),
        },
    ],
}

PRESET_PERSONAL_ADVISOR: NetworkPreset = {
    "id": "personal_advisor",
    "name": "Личный советник",
    "description": "Совет с разных сторон: аналитик → критик → психолог → итог",
    "pattern": "pipeline",
    "agents": [
        {
            "name": "Analyst",
            "system_prompt": (
                "You are a rational analyst. The user asks for advice on a personal or professional matter. "
                "Analyze the situation logically: list pros/cons, risks, and likely outcomes for each option. "
                "Be objective and data-driven. Write in the same language as the user's request."
            ),
        },
        {
            "name": "Critic",
            "system_prompt": (
                "You are a devil's advocate. You receive a rational analysis of someone's situation. "
                "Challenge the analysis: what biases might be present? What worst-case scenarios were ignored? "
                "What would a skeptic say? Be provocative but helpful. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Psychologist",
            "system_prompt": (
                "You are an empathetic psychologist. You receive a logical analysis and its critique. "
                "Now consider the emotional and psychological side: how might each option affect well-being, "
                "relationships, and personal growth? What does the person likely *want* vs what they *need*? "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Synthesizer",
            "system_prompt": (
                "You are a wise advisor. You receive three perspectives: rational analysis, critical challenge, "
                "and psychological insight. Synthesize them into a clear, balanced recommendation. "
                "Acknowledge trade-offs, suggest a concrete next step, and end with encouragement. "
                "Write in the same language as the user's request."
            ),
        },
    ],
}

PRESET_BRAINSTORM: NetworkPreset = {
    "id": "brainstorm",
    "name": "Мозговой штурм",
    "description": "3 параллельных генератора идей (разные стили) → синтез лучших",
    "pattern": "parallel",
    "agents": [
        {
            "name": "Dreamer",
            "system_prompt": (
                "You are a wild creative thinker. Given a challenge, generate 5 bold, unconventional ideas. "
                "No idea is too crazy. Think outside the box, combine unrelated concepts, break rules. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Pragmatist",
            "system_prompt": (
                "You are a practical problem-solver. Given a challenge, generate 5 realistic, implementable ideas. "
                "Focus on feasibility, cost-effectiveness, and quick wins. Each idea should have a clear first step. "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Contrarian",
            "system_prompt": (
                "You are a contrarian thinker. Given a challenge, generate 5 ideas that go against conventional wisdom. "
                "Question the premise itself. What if the problem is actually an opportunity? "
                "What would the opposite approach look like? "
                "Write in the same language as the user's request."
            ),
        },
        {
            "name": "Synthesizer",
            "system_prompt": (
                "You are an idea curator. You receive three sets of ideas: wild/creative, practical, and contrarian. "
                "Select the top 5 ideas overall, combining elements where they complement each other. "
                "For each idea: one-line summary, why it's promising, and a concrete first step to try it. "
                "Write in the same language as the user's request."
            ),
        },
    ],
}

ALL_PRESETS: dict[str, NetworkPreset] = {
    p["id"]: p
    for p in [
        PRESET_MARKET_ANALYSIS,
        PRESET_MARKETING_STRATEGY,
        PRESET_DEEP_RESEARCH,
        PRESET_CONTENT_CREATOR,
        PRESET_PERSONAL_ADVISOR,
        PRESET_BRAINSTORM,
    ]
}

DEFAULT_PRESET_ID = "deep_research"


def _build_user_message(user_input: str, previous_output: str | None) -> str:
    if previous_output is None:
        return user_input
    return (
        f"Original request:\n{user_input}\n\n"
        f"Previous agent's output:\n{previous_output}"
    )


async def run_pipeline(
    preset: NetworkPreset,
    user_input: str,
    progress_cb: ProgressCallback,
    ask_kwargs: dict = {},
) -> str:
    """Run agents sequentially; each receives user_input + previous output."""
    agents = preset["agents"]
    total = len(agents)
    output: str | None = None

    for i, agent in enumerate(agents):
        step = f"[{i + 1}/{total}] {agent['name']}"
        await progress_cb(f"{step}: работаю...")
        logger.info("Pipeline %s — running %s", preset["id"], agent["name"])

        messages = [{"role": "user", "content": _build_user_message(user_input, output)}]
        output = await ask(messages, system=agent["system_prompt"], **ask_kwargs)

        await progress_cb(f"{step}: готово ✓")

    return output or ""


async def run_parallel(
    preset: NetworkPreset,
    user_input: str,
    progress_cb: ProgressCallback,
    ask_kwargs: dict = {},
) -> str:
    """Run first N-2 agents in parallel, then synthesizer, then editor (if present)."""
    agents = preset["agents"]

    parallel_agents = [a for a in agents if "synthesizer" not in a["name"].lower() and "editor" not in a["name"].lower()]
    post_agents = [a for a in agents if a not in parallel_agents]

    total = len(parallel_agents) + len(post_agents)
    step_num = 0

    # Parallel phase
    async def _run_one(agent: AgentSpec) -> tuple[str, str]:
        messages = [{"role": "user", "content": user_input}]
        result = await ask(messages, system=agent["system_prompt"], **ask_kwargs)
        return agent["name"], result

    step_num += 1
    names = ", ".join(a["name"] for a in parallel_agents)
    await progress_cb(f"[{step_num}/{total}] {names}: работают параллельно...")
    logger.info("Parallel %s — running %d agents", preset["id"], len(parallel_agents))

    results = await asyncio.gather(*[_run_one(a) for a in parallel_agents])
    await progress_cb(f"[{step_num}/{total}] Параллельная фаза: готово ✓")

    combined = "\n\n---\n\n".join(
        f"### {name}\n{text}" for name, text in results
    )

    # Sequential post-processing (synthesizer, editor)
    output = combined
    for agent in post_agents:
        step_num += 1
        await progress_cb(f"[{step_num}/{total}] {agent['name']}: работаю...")
        logger.info("Parallel %s — running %s", preset["id"], agent["name"])

        messages = [{"role": "user", "content": _build_user_message(user_input, output)}]
        output = await ask(messages, system=agent["system_prompt"], **ask_kwargs)

        await progress_cb(f"[{step_num}/{total}] {agent['name']}: готово ✓")

    return output or ""


async def run_network(
    preset: NetworkPreset,
    user_input: str,
    progress_cb: ProgressCallback,
    ask_kwargs: dict = {},
) -> str:
    """Dispatch to the correct runner based on preset pattern."""
    if preset["pattern"] == "parallel":
        return await run_parallel(preset, user_input, progress_cb, ask_kwargs=ask_kwargs)
    return await run_pipeline(preset, user_input, progress_cb, ask_kwargs=ask_kwargs)
