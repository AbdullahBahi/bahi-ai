# -*- coding: utf-8 -*-
"""All settings, read once from environment variables (set them in Render → Environment)."""
import os


def _env(*names, default=None):
    """First non-empty value among several variable names (lets the same code read the
    names used on this machine, e.g. bahi_openrouter_key, and the conventional ones)."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip()
    return default


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# --- LLM (OpenRouter) ---
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY", "bahi_openrouter_key")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = _env("OPENROUTER_MODEL", default="~deepseek/deepseek-v4-flash-latest")
MAX_OUTPUT_TOKENS = _int("MAX_OUTPUT_TOKENS", 1200)
TEMPERATURE = float(_env("TEMPERATURE", default="0.6"))
MAX_AGENT_STEPS = _int("MAX_AGENT_STEPS", 5)       # LLM calls per customer message (tool rounds)
MAX_WEB_SEARCHES = _int("MAX_WEB_SEARCHES", 2)     # Tavily calls per customer message

# --- Tools ---
TAVILY_API_KEY = _env("TAVILY_API_KEY", "tavily_api_key")
SCENTO_MCP_URL = _env("SCENTO_MCP_URL", default="https://www.scento.com/mcp")

# --- Store ---
STORE_URL = _env("STORE_URL", default="https://bahi-perfumes.com").rstrip("/")
WHATSAPP = _env("STORE_WHATSAPP", default="+20 104 146 4663")
# Launch offer (mirrors shopify/launch_offer.py + show_offer.py). The advisor stops
# mentioning it on its own after OFFER_END; set OFFER_PCT=0 to stop it earlier.
OFFER_PCT = _int("OFFER_PCT", 20)
OFFER_END = _env("OFFER_END", default="2026-11-30T23:59:59+02:00")
OFFER_TEXT = _env("OFFER_TEXT", default="عرض الافتتاح: خصم 20% تلقائي على كل العطور حتى 30 نوفمبر")
FREE_SHIPPING_TEXT = _env("FREE_SHIPPING_TEXT", default="شحن مجاني داخل مصر للطلبات من 2000 جنيه (قبل الخصم)")

# --- Shopify app proxy ---
# The "Client secret" of the Shopify app that owns the /apps/bahi-ai proxy. Used to verify
# that requests really come through the store (Shopify signs every proxied request).
PROXY_SECRET = _env("SHOPIFY_PROXY_SECRET", "SHOPIFY_API_SECRET")
# Local development only: skips the signature check and enables /dev (a standalone preview).
DEV = _env("BAHI_AI_DEV", default="0") == "1"
# Optional absolute URL of this service (e.g. https://bahi-ai.onrender.com). When set, the
# page loads its CSS/JS straight from Render instead of through the proxy (a bit faster).
PUBLIC_BASE_URL = (_env("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL") or "").rstrip("/")

# --- Abuse / cost limits ---
MAX_USER_CHARS = _int("MAX_USER_CHARS", 800)          # one customer message
MAX_HISTORY_MESSAGES = _int("MAX_HISTORY_MESSAGES", 16)
PER_CLIENT_10MIN = _int("PER_CLIENT_10MIN", 20)       # messages per browser per 10 minutes
PER_CLIENT_DAY = _int("PER_CLIENT_DAY", 120)          # messages per browser per day
GLOBAL_PER_MIN = _int("GLOBAL_PER_MIN", 40)           # all customers together
GLOBAL_PER_DAY = _int("GLOBAL_PER_DAY", 3000)         # hard daily ceiling on LLM spend
