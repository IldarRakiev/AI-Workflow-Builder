import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

# Validate required vars at import time so failures are obvious
if not TELEGRAM_BOT_TOKEN:
    raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set. Check your .env file.")

if LLM_PROVIDER == "openrouter" and not OPENROUTER_API_KEY:
    raise EnvironmentError("OPENROUTER_API_KEY is not set but LLM_PROVIDER=openrouter.")

if LLM_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
    raise EnvironmentError("ANTHROPIC_API_KEY is not set but LLM_PROVIDER=anthropic.")

# n8n integration — optional, bot works without it
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "").rstrip("/")
N8N_API_KEY = os.getenv("N8N_API_KEY", "")

# Billing — optional, bot works without it (billing commands won't be available)
OPENROUTER_ADMIN_KEY = os.getenv("OPENROUTER_ADMIN_KEY", "")  # sk_or_mgmt_... key
FALLBACK_API_KEY = os.getenv("FALLBACK_API_KEY", "")           # cheap key for fallback
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "openai/gpt-4o-mini")
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "")  # from BotFather → Payments → ЮKassa
RUB_PER_USD = float(os.getenv("RUB_PER_USD", "95.0"))          # exchange rate for on-demand top-ups
LLM_SHARE = float(os.getenv("LLM_SHARE", "0.77"))              # fraction of gross that goes to LLM (covers ЮKassa ~3% + 20% margin)