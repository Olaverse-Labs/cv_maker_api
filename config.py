import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4-turbo"

# Only reasoning-capable models
AVAILABLE_MODELS = {
    "gpt-4-turbo": {
        "id": "openai/gpt-4-turbo",
        "name": "GPT-4 Turbo",
        "description": "Fast and efficient, best for complex reasoning tasks",
        "max_tokens": 4000,
        "temperature": 0.7
    },
    "claude-3-opus": {
        "id": "anthropic/claude-3-opus",
        "name": "Claude 3 Opus",
        "description": "Highly capable model, excellent for analysis and reasoning",
        "max_tokens": 4000,
        "temperature": 0.7
    },
    "llama-3": {
        "id": "meta-llama/llama-3-70b",
        "name": "Llama 3",
        "description": "Meta's advanced open model for reasoning and generation",
        "max_tokens": 4000,
        "temperature": 0.7
    },
    "mixtral-8x7b": {
        "id": "mistral/mixtral-8x7b",
        "name": "Mixtral 8x7B",
        "description": "Mistral's mixture-of-experts model for strong reasoning",
        "max_tokens": 4000,
        "temperature": 0.7
    }
}

# Gotenberg PDF generation settings
GOTENBERG_URL = os.getenv("GOTENBERG_URL", "http://localhost:3000")
GOTENBERG_USERNAME = os.getenv("GOTENBERG_USERNAME", "")
GOTENBERG_PASSWORD = os.getenv("GOTENBERG_PASSWORD", "") 