"""
engine/integrations/openrouter_integration.py
================================================
Thin, robust OpenRouter API client used across the UC engine.

Responsibilities:
- Round-robin across multiple API keys with automatic key rotation on 429/401.
- Enforced ``response_format: {type: json_object}`` for structured outputs.
- Timeout, retry, and exponential-backoff logic.
- JSON markdown-fence stripping (LLMs often wrap JSON in ```json ... ```).

This is the single authoritative HTTP client for all OpenRouter calls.
``discovery_bridge.py`` and ``discovery_manager.py`` both delegate here instead
of rolling their own HTTP logic.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_MODEL = "google/gemini-2.0-flash-lite:free"
_DEFAULT_TIMEOUT = 60
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds; exponential backoff applied


class OpenRouterClient:
    """
    OpenRouter chat-completions client with multi-key round-robin, retries,
    and structured JSON response enforcement.

    Parameters
    ----------
    api_keys : list[str]
        One or more OpenRouter API keys.  The client cycles through them
        automatically on rate-limit or auth errors.
    default_model : str
        Model identifier to use when the caller doesn't specify one.
    timeout : int
        HTTP request timeout in seconds.
    site_url : str
        Value for the ``HTTP-Referer`` header (OpenRouter tracks usage by site).
    site_name : str
        Value for the ``X-Title`` header.
    """

    def __init__(
        self,
        api_keys: List[str],
        default_model: str = _DEFAULT_MODEL,
        timeout: int = _DEFAULT_TIMEOUT,
        site_url: str = "https://github.com/usemanusai/UC-2",
        site_name: str = "UC — Undetected Checker",
    ):
        self._keys = [k.strip() for k in api_keys if k and k.strip()]
        self._key_index = 0
        self.default_model = default_model
        self.timeout = timeout
        self.site_url = site_url
        self.site_name = site_name

    # ── Public API ────────────────────────────────────────────────────────────

    def chat_json(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_retries: int = _MAX_RETRIES,
    ) -> Dict[str, Any]:
        """
        Send a single-turn user message and return the parsed JSON response.

        The request always sets ``response_format: {type: json_object}`` so the
        model is instructed to return valid JSON.  Markdown fences are stripped
        from the raw content before parsing as a safety measure.

        Raises
        ------
        RuntimeError
            If all API keys fail after ``max_retries`` attempts.
        """
        if not self._keys:
            raise RuntimeError("[OpenRouter] No API keys configured.")

        model = model or self.default_model
        last_error: str = "Unknown error"

        for attempt in range(max_retries):
            api_key = self._current_key()
            try:
                payload: Dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                }
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self.site_url,
                    "X-Title": self.site_name,
                }
                resp = requests.post(
                    _OPENROUTER_BASE,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )

                if resp.status_code == 429 or resp.status_code == 401:
                    logger.warning(
                        "[OpenRouter] Key %s...%s returned %d; rotating key.",
                        api_key[:8],
                        api_key[-4:],
                        resp.status_code,
                    )
                    self._rotate_key()
                    time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
                    continue

                resp.raise_for_status()
                raw_content: str = resp.json()["choices"][0]["message"]["content"]
                return _parse_json_content(raw_content)

            except requests.exceptions.Timeout as exc:
                last_error = f"Timeout on attempt {attempt + 1}: {exc}"
                logger.warning("[OpenRouter] %s", last_error)
                time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error on attempt {attempt + 1}: {exc}"
                logger.warning("[OpenRouter] %s", last_error)
                break  # Malformed JSON is not retriable with same key
            except Exception as exc:
                last_error = f"Unexpected error on attempt {attempt + 1}: {exc}"
                logger.warning("[OpenRouter] %s", last_error)
                time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

        raise RuntimeError(f"[OpenRouter] All attempts failed. Last error: {last_error}")

    def chat_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_retries: int = _MAX_RETRIES,
    ) -> str:
        """
        Send a single-turn user message and return the raw text response.

        Unlike ``chat_json``, this does NOT enforce JSON response format.
        """
        if not self._keys:
            raise RuntimeError("[OpenRouter] No API keys configured.")

        model = model or self.default_model
        last_error: str = "Unknown error"

        for attempt in range(max_retries):
            api_key = self._current_key()
            try:
                payload: Dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                }
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self.site_url,
                    "X-Title": self.site_name,
                }
                resp = requests.post(
                    _OPENROUTER_BASE,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )

                if resp.status_code in (429, 401):
                    self._rotate_key()
                    time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
                    continue

                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]

            except Exception as exc:
                last_error = str(exc)
                time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

        raise RuntimeError(f"[OpenRouter] All attempts failed. Last error: {last_error}")

    # ── Key rotation ──────────────────────────────────────────────────────────

    def _current_key(self) -> str:
        return self._keys[self._key_index % len(self._keys)]

    def _rotate_key(self) -> None:
        self._key_index = (self._key_index + 1) % len(self._keys)
        logger.info(
            "[OpenRouter] Rotated to key index %d / %d",
            self._key_index,
            len(self._keys),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json_content(content: str) -> Dict[str, Any]:
    """Strip markdown fences and parse JSON from LLM response content."""
    stripped = content.strip()
    if "```json" in stripped:
        stripped = stripped.split("```json")[1].split("```")[0].strip()
    elif "```" in stripped:
        stripped = stripped.split("```")[1].split("```")[0].strip()
    return json.loads(stripped)
