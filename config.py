import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-5.6-terra"

# Seconds to wait on an OpenRouter call. Without this a hung connection holds a
# worker thread forever; long CVs on slow models can legitimately take a while.
OPENROUTER_TIMEOUT = int(os.getenv("OPENROUTER_TIMEOUT", "120"))

# Token ceiling for the CV analysis step. This was 2000, which truncated the
# analysis JSON mid-string on longer CVs and blew up the JSON parse. max_tokens
# is a cap, not a spend, so raising it costs nothing on shorter replies.
ANALYSIS_MAX_TOKENS = int(os.getenv("ANALYSIS_MAX_TOKENS", "6000"))

# Sent to OpenRouter for dashboard attribution. Both optional.
APP_URL = os.getenv("APP_URL", "")
APP_TITLE = os.getenv("APP_TITLE", "CV Maker API")

# Largest upload accepted per file, in bytes. Guards against a huge PDF being
# read straight into memory.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024

# Timezone the cover letter date falls back to when the caller names none. The
# container clock is UTC, which prints yesterday's date for applicants far enough
# east of it, so a service with a known audience should set this to their zone.
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "UTC")

# Comma-separated list of allowed CORS origins, or "*" for any.
CORS_ALLOW_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()
]

# Reasoning-capable models suited to CV rewriting, ATS optimisation and
# long-form cover letter generation. All IDs verified against OpenRouter.
# Flagship tiers (Opus, GPT-5.6 Sol, GPT-4 Turbo) are deliberately excluded on
# cost grounds. Prices are USD per million tokens.
AVAILABLE_MODELS = {
    "claude-sonnet-5": {
        "id": "anthropic/claude-sonnet-5",
        "name": "Claude Sonnet 5",
        "description": "Best all-round choice: strong professional writing, reliable JSON and HTML output",
        "context_length": 1000000,
        "tier": "balanced",
        "price_per_m": {"input": 2.00, "output": 10.00},
        "max_tokens": 8000,
        "temperature": 0.7
    },
    "gpt-5.6-terra": {
        "id": "openai/gpt-5.6-terra",
        "name": "GPT-5.6 Terra",
        "description": "Strong OpenAI model for tailoring CVs to job descriptions, mid-tier pricing. Also serves all retired model keys",
        "context_length": 1050000,
        "tier": "balanced",
        "price_per_m": {"input": 1.00, "output": 6.00},
        "max_tokens": 8000,
        "temperature": 0.7
    },
    "grok-4.5": {
        "id": "x-ai/grok-4.5",
        "name": "Grok 4.5",
        "description": "Capable general model with a punchier, less formulaic writing style",
        "context_length": 500000,
        "tier": "balanced",
        "price_per_m": {"input": 2.00, "output": 6.00},
        "max_tokens": 8000,
        "temperature": 0.7
    },
    "claude-haiku-4.5": {
        "id": "anthropic/claude-haiku-4.5",
        "name": "Claude Haiku 4.5",
        "description": "Fast, cheap Claude model for structured extraction and short cover letters",
        "context_length": 200000,
        "tier": "fast",
        "price_per_m": {"input": 1.00, "output": 5.00},
        "max_tokens": 8000,
        "temperature": 0.7
    },
    "gpt-5.4-mini": {
        "id": "openai/gpt-5.4-mini",
        "name": "GPT-5.4 Mini",
        "description": "Low-cost OpenAI model, good quality for routine CV rewrites",
        "context_length": 400000,
        "tier": "fast",
        "price_per_m": {"input": 0.75, "output": 4.50},
        "max_tokens": 8000,
        "temperature": 0.7
    },
    "gemini-3.5-flash-lite": {
        "id": "google/gemini-3.5-flash-lite",
        "name": "Gemini 3.5 Flash Lite",
        "description": "Very fast and inexpensive, good for bulk generation and quick drafts",
        "context_length": 1048576,
        "tier": "budget",
        "price_per_m": {"input": 0.30, "output": 2.50},
        "max_tokens": 8000,
        "temperature": 0.7
    },
    "deepseek-v4-pro": {
        "id": "deepseek/deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "description": "Cheapest option with solid reasoning, best value for high-volume use",
        "context_length": 1048576,
        "tier": "budget",
        "price_per_m": {"input": 0.44, "output": 0.87},
        "max_tokens": 8000,
        "temperature": 0.7
    }
}

# Model serving retired keys. Old clients still send model names that no longer
# exist on OpenRouter; every one of them is routed here rather than failing.
LEGACY_MODEL = "gpt-5.6-terra"

# Old model keys that clients may still send, all mapped to LEGACY_MODEL.
LEGACY_MODEL_ALIASES = {
    "gpt-4-turbo": LEGACY_MODEL,
    "gpt-4": LEGACY_MODEL,
    "gpt-3.5-turbo": LEGACY_MODEL,
    "claude-3-opus": LEGACY_MODEL,
    "claude-3-sonnet": LEGACY_MODEL,
    "claude-3-haiku": LEGACY_MODEL,
    "gemini-pro": LEGACY_MODEL,
    "llama-3": LEGACY_MODEL,
    "mixtral-8x7b": LEGACY_MODEL
}


# Ceiling for a generation call when the model is not one we list. Generous on
# purpose: max_tokens is a cap, not a spend, and a CV cut off mid-tag is a far
# worse outcome than an unused allowance.
FALLBACK_MAX_TOKENS = 8000


def resolve_model(model_key):
    """Return the OpenRouter model ID for a client-supplied key, or the default."""
    if not model_key:
        return DEFAULT_MODEL
    model_key = LEGACY_MODEL_ALIASES.get(model_key, model_key)
    if model_key in AVAILABLE_MODELS:
        return AVAILABLE_MODELS[model_key]['id']
    return DEFAULT_MODEL


def model_max_tokens(model_id):
    """Output cap for a model ID, from the table that /available-models serves.

    The per-model `max_tokens` used to be advertised to clients and then ignored
    at the call site, which is how CV generation ended up hardcoded at 4000 —
    low enough to cut a long CV off mid-tag.
    """
    for model in AVAILABLE_MODELS.values():
        if model['id'] == model_id:
            return model['max_tokens']
    return FALLBACK_MAX_TOKENS


# Gotenberg PDF generation settings
GOTENBERG_URL = os.getenv("GOTENBERG_URL", "http://localhost:3000")
# Seconds to wait on a conversion. Same reasoning as OPENROUTER_TIMEOUT: without
# one, a hung Gotenberg holds a worker thread for the life of the process.
GOTENBERG_TIMEOUT = int(os.getenv("GOTENBERG_TIMEOUT", "60"))
GOTENBERG_USERNAME = os.getenv("GOTENBERG_USERNAME", "")
GOTENBERG_PASSWORD = os.getenv("GOTENBERG_PASSWORD", "")
