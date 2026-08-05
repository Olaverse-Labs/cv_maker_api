"""Single place where the app talks to OpenRouter.

Every generation endpoint used to inline its own requests.post with no timeout
and no status check, so an upstream error surfaced as a KeyError on
result['choices']. This module keeps the outbound payload exactly as it was and
adds the missing failure handling.
"""
import logging

import requests

from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_TIMEOUT,
    APP_TITLE,
    APP_URL,
)

logger = logging.getLogger(__name__)


class OpenRouterError(Exception):
    """Upstream call failed in a way the caller should turn into an error response."""


def _headers():
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    # Optional attribution; shows this app in OpenRouter's dashboard rankings.
    if APP_URL:
        headers["HTTP-Referer"] = APP_URL
    if APP_TITLE:
        headers["X-Title"] = APP_TITLE
    return headers


def chat_completion(model: str, prompt: str, temperature: float, max_tokens: int) -> str:
    """Send one prompt to OpenRouter and return the message content.

    Raises OpenRouterError with a readable message on any failure.
    """
    if not OPENROUTER_API_KEY:
        raise OpenRouterError("OPENROUTER_API_KEY is not configured")

    data = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    try:
        response = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=_headers(),
            json=data,
            timeout=OPENROUTER_TIMEOUT
        )
    except requests.exceptions.Timeout:
        logger.warning("openrouter timeout model=%s", model)
        raise OpenRouterError(f"Model request timed out after {OPENROUTER_TIMEOUT}s")
    except requests.exceptions.RequestException as e:
        logger.warning("openrouter transport error model=%s err=%s", model, e)
        raise OpenRouterError(f"Could not reach the model provider: {e}")

    if response.status_code != 200:
        detail = _error_detail(response)
        logger.warning("openrouter http %s model=%s detail=%s",
                       response.status_code, model, detail)
        raise OpenRouterError(
            f"Model provider returned {response.status_code}: {detail}"
        )

    try:
        result = response.json()
    except ValueError:
        raise OpenRouterError("Model provider returned a non-JSON response")

    # A 200 can still carry an error body instead of choices.
    if 'error' in result and 'choices' not in result:
        raise OpenRouterError(f"Model provider error: {_stringify(result['error'])}")

    try:
        return result['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise OpenRouterError("Model provider returned no completion")


def _error_detail(response):
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:200]
    if isinstance(body, dict) and 'error' in body:
        return _stringify(body['error'])
    return str(body)[:200]


def _stringify(err):
    if isinstance(err, dict):
        return str(err.get('message') or err)[:200]
    return str(err)[:200]
