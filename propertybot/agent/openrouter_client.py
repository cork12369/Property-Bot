"""OpenRouter chat client (stdlib only, JSON mode with retries)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .prompts import SYSTEM_PROMPT

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    pass


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rsplit("```", 1)[0].strip():
            text = text.rsplit("```", 1)[0]
    return text.strip()


def chat_json(
    user_prompt: str,
    *,
    api_key: str,
    model: str,
    timeout: int = 60,
    retries: int = 3,
    system_prompt: str = SYSTEM_PROMPT,
) -> dict:
    """Call OpenRouter and return the parsed JSON object from the reply."""
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                API_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/property-bot",
                    "X-Title": "PropertyBot agent harness",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            return json.loads(_strip_fences(content))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            if error.code == 401:
                raise OpenRouterError(
                    "OpenRouter returned 401 — OPENROUTER_API_KEY is invalid."
                ) from error
            if error.code == 404:
                raise OpenRouterError(
                    f"OpenRouter returned 404 — model '{model}' not found. "
                    f"Check OPENROUTER_MODEL. Detail: {detail}"
                ) from error
            last_error = OpenRouterError(
                f"OpenRouter HTTP {error.code}: {detail}"
            )
        except (KeyError, IndexError, json.JSONDecodeError, ValueError) as error:
            last_error = OpenRouterError(
                f"OpenRouter reply was not valid JSON: {error}"
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = OpenRouterError(f"OpenRouter request failed: {error}")
        if attempt < retries:
            time.sleep(2 * attempt)

    raise last_error or OpenRouterError("OpenRouter request failed.")
