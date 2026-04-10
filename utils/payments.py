import logging
from typing import TypedDict

from telegram import Bot, LabeledPrice

from utils.billing import TIER_CONFIG

logger = logging.getLogger(__name__)


class TierInfo(TypedDict):
    id: str
    rubles: int
    label: str
    description: str


TIERS: list[TierInfo] = [
    TierInfo(
        id="basic",
        rubles=490,
        label="Basic — 490 ₽/мес",
        description="$4 LLM-бюджет · до $1/нед · безлимитный QA",
    ),
    TierInfo(
        id="pro",
        rubles=990,
        label="Pro — 990 ₽/мес",
        description="$8 LLM-бюджет · до $2/нед · приоритетная обработка",
    ),
    TierInfo(
        id="max",
        rubles=1990,
        label="Max — 1990 ₽/мес",
        description="$16 LLM-бюджет · до $4/нед · максимальный ресурс",
    ),
]

_TIER_BY_ID: dict[str, TierInfo] = {t["id"]: t for t in TIERS}


async def send_tier_invoice(bot: Bot, chat_id: int, tier_id: str) -> None:
    """Send a ЮKassa invoice for the chosen subscription tier."""
    from config import PAYMENT_PROVIDER_TOKEN
    tier = _TIER_BY_ID.get(tier_id)
    if not tier:
        raise ValueError(f"Unknown tier: {tier_id!r}")

    await bot.send_invoice(
        chat_id=chat_id,
        title=tier["label"],
        description=tier["description"],
        payload=f"tier:{tier_id}",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=tier["label"], amount=tier["rubles"] * 100)],  # kopecks
    )
    logger.info("Sent %s invoice to chat %s (%d RUB)", tier_id, chat_id, tier["rubles"])


async def send_topup_invoice(bot: Bot, chat_id: int, rubles: int) -> None:
    """Send a ЮKassa invoice for a one-time top-up."""
    from config import PAYMENT_PROVIDER_TOKEN, RUB_PER_USD, LLM_SHARE
    if rubles < 100:
        raise ValueError("Minimum top-up is 100 RUB")

    usd_approx = round(rubles / RUB_PER_USD, 2)
    llm_usd = round(usd_approx * LLM_SHARE, 2)

    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Пополнение — {rubles} ₽",
        description=f"≈ ${usd_approx:.2f} · добавится к LLM-бюджету: ${llm_usd:.2f}",
        payload=f"topup:{rubles}",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=f"{rubles} ₽", amount=rubles * 100)],  # kopecks
    )
    logger.info("Sent topup invoice to chat %s (%d RUB)", chat_id, rubles)


def parse_payment_payload(payload: str) -> tuple[str, str | int]:
    """Parse payment payload. Returns ("tier", "basic") or ("topup", 500)."""
    if payload.startswith("tier:"):
        return ("tier", payload.removeprefix("tier:"))
    if payload.startswith("topup:"):
        return ("topup", int(payload.removeprefix("topup:")))
    raise ValueError(f"Unknown payment payload: {payload!r}")
