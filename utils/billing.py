import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_KEYS_FILE = _DATA_DIR / "user_keys.json"
_OR_BASE = "https://openrouter.ai/api/v1"

TIER_CONFIG: dict[str, dict] = {
    # rubles price; LLM budget is fixed (we absorb exchange rate risk for subscriptions)
    "basic": {"rubles": 490,  "monthly_budget": 4.0,  "weekly_limit": 1.0,  "daily_limit": 0.14},
    "pro":   {"rubles": 990,  "monthly_budget": 8.0,  "weekly_limit": 2.0,  "daily_limit": 0.29},
    "max":   {"rubles": 1990, "monthly_budget": 16.0, "weekly_limit": 4.0,  "daily_limit": 0.57},
}


class UserKeyRecord(TypedDict):
    key_hash: str
    key_value: str
    tier: str
    monthly_budget: float
    weekly_limit: float
    daily_limit: float
    activated_at: str
    next_weekly_reset: str
    usage_at_last_reset: float
    disabled: bool


class KeyCheckResult(TypedDict):
    key_hash: str
    usage: float
    limit: float
    limit_remaining: float
    disabled: bool


# ---------------------------------------------------------------------------
# JSON persistence helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if not _KEYS_FILE.exists():
        return {}
    try:
        return json.loads(_KEYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_KEYS_FILE)


def _next_monday() -> str:
    """Return ISO string of the coming Monday 00:00 UTC (or next week if today is Monday)."""
    now = datetime.now(timezone.utc)
    days_ahead = (7 - now.weekday()) % 7 or 7  # 0 = Monday; always go forward at least 1 week
    next_mon = (now + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_mon.isoformat()


# ---------------------------------------------------------------------------
# OpenRouter Management API helpers
# ---------------------------------------------------------------------------

def _or_headers() -> dict:
    from config import OPENROUTER_ADMIN_KEY
    if not OPENROUTER_ADMIN_KEY:
        raise RuntimeError("OPENROUTER_ADMIN_KEY is not set — billing is unavailable")
    return {"Authorization": f"Bearer {OPENROUTER_ADMIN_KEY}", "Content-Type": "application/json"}


async def _or_post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{_OR_BASE}{path}", json=payload, headers=_or_headers())
        r.raise_for_status()
        return r.json()


async def _or_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{_OR_BASE}{path}", headers=_or_headers())
        r.raise_for_status()
        return r.json()


async def _or_patch(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.patch(f"{_OR_BASE}{path}", json=payload, headers=_or_headers())
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_user_key(user_id: int) -> UserKeyRecord | None:
    """Return the stored key record for a user, or None if not found."""
    return _load().get(str(user_id))


async def provision_key(user_id: int, tier: str) -> UserKeyRecord:
    """Create a new OpenRouter key for the user at the given tier."""
    if tier not in TIER_CONFIG:
        raise ValueError(f"Unknown tier: {tier!r}")

    cfg = TIER_CONFIG[tier]
    resp = await _or_post("/keys", {
        "name": f"user_{user_id}_{tier}",
        "limit": cfg["monthly_budget"],
        "limit_reset": "monthly",
    })

    key_value: str = resp["key"]
    key_hash: str = resp["data"]["hash"]

    record: UserKeyRecord = {
        "key_hash": key_hash,
        "key_value": key_value,
        "tier": tier,
        "monthly_budget": cfg["monthly_budget"],
        "weekly_limit": cfg["weekly_limit"],
        "daily_limit": cfg["daily_limit"],
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "next_weekly_reset": _next_monday(),
        "usage_at_last_reset": 0.0,
        "disabled": False,
    }

    data = _load()
    data[str(user_id)] = record
    _save(data)

    logger.info("Provisioned %s key for user %s (hash=%s)", tier, user_id, key_hash)
    return record


async def top_up_key(user_id: int, rubles: int) -> UserKeyRecord:
    """Add budget to existing key (or provision basic if none). Rubles → USD at RUB_PER_USD * LLM_SHARE."""
    from config import RUB_PER_USD, LLM_SHARE
    added_usd = round((rubles / RUB_PER_USD) * LLM_SHARE, 4)

    data = _load()
    record = data.get(str(user_id))

    if not record:
        # First payment ever — provision a basic key
        record = await provision_key(user_id, "basic")
        data = _load()
        record = data[str(user_id)]

    new_budget = round(record["monthly_budget"] + added_usd, 4)
    await _or_patch(f"/keys/{record['key_hash']}", {"limit": new_budget})

    record["monthly_budget"] = new_budget
    record["weekly_limit"] = round(new_budget / 4, 4)
    record["daily_limit"] = round(new_budget / 28, 4)

    if record.get("disabled"):
        await _or_patch(f"/keys/{record['key_hash']}", {"disabled": False})
        record["disabled"] = False

    data[str(user_id)] = record
    _save(data)

    logger.info(
        "Topped up key for user %s (+%.4f USD → budget=%.4f)", user_id, added_usd, new_budget
    )
    return record


async def get_key_usage(key_hash: str) -> KeyCheckResult:
    """Fetch current usage stats from OpenRouter Keys API."""
    resp = await _or_get(f"/keys/{key_hash}")
    d = resp.get("data", resp)
    return KeyCheckResult(
        key_hash=key_hash,
        usage=float(d.get("usage", 0.0)),
        limit=float(d.get("limit", 0.0)),
        limit_remaining=float(d.get("limit_remaining", 0.0)),
        disabled=bool(d.get("disabled", False)),
    )


async def disable_key(key_hash: str) -> bool:
    try:
        await _or_patch(f"/keys/{key_hash}", {"disabled": True})
        return True
    except Exception as e:
        logger.error("Failed to disable key %s: %s", key_hash, e)
        return False


async def enable_key(key_hash: str) -> bool:
    try:
        await _or_patch(f"/keys/{key_hash}", {"disabled": False})
        return True
    except Exception as e:
        logger.error("Failed to enable key %s: %s", key_hash, e)
        return False


async def check_and_reset_weekly_limits() -> None:
    """Hourly background task: re-enable keys whose weekly reset date has passed."""
    data = _load()
    now = datetime.now(timezone.utc)
    changed = False

    for uid_str, record in data.items():
        try:
            next_reset = datetime.fromisoformat(record["next_weekly_reset"])
            if now >= next_reset and record.get("disabled"):
                # Snapshot usage so we can track next week's delta
                try:
                    usage_info = await get_key_usage(record["key_hash"])
                    record["usage_at_last_reset"] = usage_info["usage"]
                except Exception:
                    pass

                await enable_key(record["key_hash"])
                record["disabled"] = False
                record["next_weekly_reset"] = _next_monday()
                data[uid_str] = record
                changed = True
                logger.info("Weekly reset: re-enabled key for user %s", uid_str)
        except Exception as e:
            logger.warning("Weekly reset error for user %s: %s", uid_str, e)

    if changed:
        _save(data)


async def check_weekly_limit_for_user(user_id: int) -> bool:
    """Check if user's weekly spend is over limit; disable key if so. Returns True if active."""
    record = get_user_key(user_id)
    if not record:
        return True

    if record.get("disabled"):
        # Check if reset is due
        try:
            next_reset = datetime.fromisoformat(record["next_weekly_reset"])
            if datetime.now(timezone.utc) >= next_reset:
                await check_and_reset_weekly_limits()
                record = get_user_key(user_id)
        except Exception:
            pass
        return not (record or {}).get("disabled", False)

    try:
        usage_info = await get_key_usage(record["key_hash"])
        usage_since_reset = usage_info["usage"] - record.get("usage_at_last_reset", 0.0)

        if usage_since_reset >= record["weekly_limit"]:
            data = _load()
            data[str(user_id)]["disabled"] = True
            _save(data)
            await disable_key(record["key_hash"])
            logger.info(
                "Weekly limit hit for user %s (%.4f >= %.4f)",
                user_id, usage_since_reset, record["weekly_limit"],
            )
            return False
    except Exception as e:
        logger.warning("Could not check weekly limit for user %s: %s", user_id, e)

    return True
