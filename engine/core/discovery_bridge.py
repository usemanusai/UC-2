"""
engine/core/discovery_bridge.py
================================
Typed adapter between the AI discovery pipeline and the Tkinter GUI.

``run_and_validate`` performs the full discovery flow:
1. Fetches the target page HTML using a lightweight HTTP request
2. Extracts DOM element metadata (inputs, buttons, error containers)
3. Sends the DOM snapshot to OpenRouter or Claude proxy for AI analysis
4. Validates and returns a DiscoveryResult object

``run_and_validate_cached`` wraps ``run_and_validate`` with an optional
SQLite cache so repeated runs against the same URL skip the AI call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from typing import Callable, Dict, List, Optional, Union

from engine.core.discovery_schema import (
    DiscoveryResult,
    DiscoveryValidationError,
    parse_result,
)

logger = logging.getLogger(__name__)


# ── AI Query Helper ──────────────────────────────────────────────────────────


def _ask_ai(
    prompt: str,
    api_keys: List[str],
    preferred_model: str = "",
    claude_proxy_url: str = "",
    claude_proxy_model: str = "",
    claude_proxy_enabled: bool = False,
) -> dict:
    """
    Send a prompt to OpenRouter or Claude proxy and return the parsed JSON response.
    Tries Claude proxy first (if enabled), then iterates through OpenRouter keys.
    """
    import requests

    # 1. Try Claude proxy
    if claude_proxy_enabled and claude_proxy_url:
        try:
            url = claude_proxy_url
            if not url.endswith("/chat/completions") and not url.endswith(
                "/v1/chat/completions"
            ):
                url = url.rstrip("/") + "/v1/chat/completions"

            headers = {"Content-Type": "application/json"}
            payload = {
                "model": claude_proxy_model or "gemini-3-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            }
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            if response.status_code == 200:
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"]
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                return json.loads(content.strip())
        except Exception as e:
            logger.warning("[Bridge] Claude proxy failed, trying OpenRouter: %s", e)

    # 2. Try OpenRouter keys
    errors = []
    for api_key in api_keys:
        if not api_key:
            continue
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/usemanusai/UC",
                "X-Title": "Universal Checker",
            }
            payload = {
                "model": preferred_model
                or "google/gemini-2.0-flash-lite-preview-02-05:free",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            if response.status_code == 200:
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"]
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                return json.loads(content.strip())
            else:
                errors.append(
                    f"OpenRouter {response.status_code}: {response.text[:200]}"
                )
        except Exception as e:
            errors.append(str(e))

    raise RuntimeError(f"AI query failed. Errors: {'; '.join(errors)}")


# ── HTML Fetcher ─────────────────────────────────────────────────────────────


def _fetch_page_html(target_url: str, log_callback: Callable) -> str:
    """
    Fetch the HTML source of the target URL using a lightweight HTTP request.
    Falls back to a minimal HTML string if the fetch fails.
    """
    import requests

    log_callback("[Bridge] Fetching page HTML...")
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(
            target_url, headers=headers, timeout=30, allow_redirects=True
        )
        resp.raise_for_status()
        html = resp.text
        log_callback(
            f"[Bridge] Fetched {len(html)} chars of HTML (status {resp.status_code})."
        )
        return html
    except Exception as e:
        log_callback(
            f"[Bridge] HTML fetch failed: {e}. AI will work from URL context only."
        )
        return ""


def _extract_dom_elements(html: str) -> list:
    """
    Extract interactive DOM elements (inputs, buttons, forms, error containers)
    from raw HTML using regex-based parsing (no browser needed).
    """
    elements = []

    # Extract <input> elements
    for m in re.finditer(r"<input\b([^>]*)/?>", html, re.IGNORECASE | re.DOTALL):
        attrs = m.group(1)
        el = {
            "tag": "INPUT",
            "id": _extract_attr(attrs, "id"),
            "name": _extract_attr(attrs, "name"),
            "type": _extract_attr(attrs, "type") or "text",
            "class": _extract_attr(attrs, "class"),
            "placeholder": _extract_attr(attrs, "placeholder"),
            "outerHTML": m.group(0)[:300],
        }
        elements.append(el)

    # Extract <button> elements
    for m in re.finditer(
        r"<button\b([^>]*)>(.*?)</button>", html, re.IGNORECASE | re.DOTALL
    ):
        attrs = m.group(1)
        inner = re.sub(r"<[^>]+>", "", m.group(2)).strip()[:100]
        el = {
            "tag": "BUTTON",
            "id": _extract_attr(attrs, "id"),
            "name": _extract_attr(attrs, "name"),
            "type": _extract_attr(attrs, "type") or "submit",
            "class": _extract_attr(attrs, "class"),
            "text": inner,
            "outerHTML": m.group(0)[:300],
        }
        elements.append(el)

    # Extract <a> elements that look like buttons (role="button" or class contains "btn")
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.IGNORECASE | re.DOTALL):
        attrs = m.group(1)
        cls = _extract_attr(attrs, "class") or ""
        role = _extract_attr(attrs, "role") or ""
        if "btn" in cls.lower() or "button" in cls.lower() or role.lower() == "button":
            inner = re.sub(r"<[^>]+>", "", m.group(2)).strip()[:100]
            el = {
                "tag": "A",
                "id": _extract_attr(attrs, "id"),
                "class": cls,
                "role": role,
                "text": inner,
                "outerHTML": m.group(0)[:300],
            }
            elements.append(el)

    # Extract <form> elements
    for m in re.finditer(r"<form\b([^>]*)>", html, re.IGNORECASE | re.DOTALL):
        attrs = m.group(1)
        el = {
            "tag": "FORM",
            "id": _extract_attr(attrs, "id"),
            "action": _extract_attr(attrs, "action"),
            "method": _extract_attr(attrs, "method"),
            "class": _extract_attr(attrs, "class"),
            "outerHTML": m.group(0)[:300],
        }
        elements.append(el)

    # Extract <select> elements
    for m in re.finditer(r"<select\b([^>]*)>", html, re.IGNORECASE | re.DOTALL):
        attrs = m.group(1)
        el = {
            "tag": "SELECT",
            "id": _extract_attr(attrs, "id"),
            "name": _extract_attr(attrs, "name"),
            "class": _extract_attr(attrs, "class"),
            "outerHTML": m.group(0)[:300],
        }
        elements.append(el)

    # Extract potential error containers (divs/spans with error-related classes/ids)
    error_pattern = re.compile(
        r"<(?:div|span|p|label|section)\b([^>]*(?:error|alert|danger|warning|invalid|captcha|fail|wrong|incorrect)[^>]*)>(.*?)</(?:div|span|p|label|section)>",
        re.IGNORECASE | re.DOTALL,
    )
    for m in error_pattern.finditer(html):
        attrs = m.group(1)
        inner = re.sub(r"<[^>]+>", "", m.group(2)).strip()[:200]
        el = {
            "tag": "ERROR_CONTAINER",
            "id": _extract_attr(attrs, "id"),
            "class": _extract_attr(attrs, "class"),
            "text": inner,
            "outerHTML": m.group(0)[:300],
        }
        elements.append(el)

    return elements[:150]  # Cap at 150 elements to avoid token overflow


def _extract_attr(attrs_str: str, attr_name: str) -> str:
    """Extract a single HTML attribute value from an attribute string."""
    # Match attr="value" or attr='value'
    m = re.search(
        rf"""{attr_name}\s*=\s*["']([^"']*)["']""",
        attrs_str,
        re.IGNORECASE,
    )
    return m.group(1) if m else ""


# ── Discovery Prompt ─────────────────────────────────────────────────────────

_DISCOVERY_PROMPT_TEMPLATE = """You are an AI CSS selector discovery agent. You are analyzing the HTML source of a login page to identify the exact CSS selectors for each interactive form field.

Target URL: {target_url}

Here are the interactive DOM elements extracted from the page HTML:
```json
{dom_elements_json}
```

Your goal is to identify the exact CSS selectors for the login form fields.

CRITICAL INSTRUCTIONS:
- Provide REAL CSS selectors only (e.g. "#email", "input[name='email']", ".login-form button[type='submit']")
- Do NOT hallucinate selectors. Only provide selectors for elements that EXIST in the DOM data above.
- If you cannot find a field, set it to null.
- Prefer selectors by ID (#id), then by name (input[name='...']), then by unique class (.class), then by type+position.
- For error containers, look for divs/spans with classes containing "error", "alert", "danger", "invalid", etc.

Response MUST be a JSON object matching this exact format:
{{
  "email_field": "CSS selector for email/username input field (or null)",
  "password_field": "CSS selector for password input field (or null)",
  "submit_button": "CSS selector for submit/login button (or null)",
  "next_button": "CSS selector for intermediate 'Next' step button if multi-step login (or null)",
  "invalid_error_selector": "CSS selector for incorrect credentials error message container (or null)",
  "invalid_inner_html": "The expected inner text of the invalid credentials error (or null)",
  "invalid_outer_html": "A snippet of the outer HTML of the invalid credentials error container (or null)",
  "captcha_error_selector": "CSS selector for CAPTCHA error message container (or null)",
  "captcha_inner_html": "The expected inner text of the CAPTCHA error (or null)",
  "captcha_outer_html": "A snippet of the outer HTML of the CAPTCHA error container (or null)",
  "redirect_url": "The URL the page redirects to after successful login (or null)",
  "login_url": "{target_url}",
  "auth_pattern": 1
}}

auth_pattern values:
1 = Single page (email + password on same page)
2 = Multi-step (email first, then password on next page)
3 = Email + password + CAPTCHA
4 = Multi-step with CAPTCHA
5 = SSO/OAuth only
6 = Magic link / passwordless
7 = OTP/MFA required
8 = Other/custom

Return ONLY the JSON object, no markdown, no explanation.
"""


# ── Main Discovery Functions ─────────────────────────────────────────────────


def run_and_validate(
    target_url: str,
    api_keys: List[str],
    log_callback: Optional[Callable] = None,
    preferred_model: str = "",
    use_database: bool = True,
    claude_proxy_url: str = "",
    claude_proxy_model: str = "",
    claude_proxy_enabled: bool = False,
) -> DiscoveryResult:
    """
    Run the full auto-discovery pipeline and validate the output.

    Parameters
    ----------
    target_url:
        The login page URL to analyse.
    api_keys:
        List of OpenRouter API keys to use. May be empty when Claude proxy is enabled.
    log_callback:
        Optional callable for progress messages.
    preferred_model:
        OpenRouter model ID to prefer.
    use_database:
        Whether to cache results in SQLite.
    claude_proxy_url:
        URL of the Claude proxy server.
    claude_proxy_model:
        Model to use with the Claude proxy.
    claude_proxy_enabled:
        Whether the Claude proxy is enabled.

    Returns
    -------
    DiscoveryResult
        A fully validated discovery result.

    Raises
    ------
    DiscoveryValidationError
        If the AI output fails schema validation.
    RuntimeError
        If the AI query fails entirely.
    """

    def _safe_log(msg: str) -> None:
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass
        logger.info(msg)

    _safe_log(f"[Bridge] Starting discovery for: {target_url}")

    # Step 1: Fetch page HTML
    html = _fetch_page_html(target_url, _safe_log)

    # Step 2: Extract DOM elements
    dom_elements = _extract_dom_elements(html) if html else []
    _safe_log(f"[Bridge] Extracted {len(dom_elements)} interactive DOM elements.")

    # If we couldn't extract any elements from HTML, provide URL context only
    if not dom_elements:
        dom_elements_json = json.dumps(
            [
                {
                    "tag": "NOTE",
                    "text": "Could not fetch page HTML. Please analyze the URL pattern and common login form structures for this domain.",
                }
            ],
            indent=2,
        )
    else:
        dom_elements_json = json.dumps(dom_elements, indent=2)

    # Step 3: Build and send AI prompt
    prompt = _DISCOVERY_PROMPT_TEMPLATE.format(
        target_url=target_url,
        dom_elements_json=dom_elements_json,
    )

    _safe_log("[Bridge] Sending DOM snapshot to AI for analysis...")
    try:
        raw = _ask_ai(
            prompt=prompt,
            api_keys=api_keys,
            preferred_model=preferred_model,
            claude_proxy_url=claude_proxy_url,
            claude_proxy_model=claude_proxy_model,
            claude_proxy_enabled=claude_proxy_enabled,
        )
    except Exception as e:
        logger.error("[Bridge] AI query raised: %s", e)
        raise RuntimeError(f"Discovery pipeline failed for {target_url}: {e}") from e

    _safe_log(f"[Bridge] Raw output received ({len(raw)} keys). Validating...")

    # Step 4: Validate through schema
    try:
        result = parse_result(raw)
    except DiscoveryValidationError:
        logger.warning(
            "[Bridge] Schema validation failed. Raw keys: %s. Errors forwarded to caller.",
            list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
        )
        raise

    _safe_log("[Bridge] Discovery validated successfully.")
    return result


def run_and_validate_cached(
    target_url: str,
    api_keys: List[str],
    log_callback: Optional[Callable] = None,
    preferred_model: str = "",
    use_database: bool = True,
    claude_proxy_url: str = "",
    claude_proxy_model: str = "",
    claude_proxy_enabled: bool = False,
) -> DiscoveryResult:
    """
    Wrapper around ``run_and_validate`` with optional SQLite caching.

    If ``use_database`` is True and a valid cached result exists for
    ``target_url`` (less than 7 days old), it is returned immediately
    without making any AI calls.
    """
    # Try loading from cache first
    if use_database:
        try:
            cached = _load_from_cache(target_url)
            if cached is not None:
                if log_callback:
                    log_callback(
                        f"[Bridge] Loaded cached discovery result for {target_url}"
                    )
                return cached
        except Exception as e:
            logger.warning("[Bridge] Cache read failed: %s", e)

    # Run fresh discovery
    result = run_and_validate(
        target_url=target_url,
        api_keys=api_keys,
        log_callback=log_callback,
        preferred_model=preferred_model,
        use_database=use_database,
        claude_proxy_url=claude_proxy_url,
        claude_proxy_model=claude_proxy_model,
        claude_proxy_enabled=claude_proxy_enabled,
    )

    # Save to cache
    if use_database:
        try:
            _save_to_cache(target_url, result)
            if log_callback:
                log_callback("[Bridge] Discovery result cached to database.")
        except Exception as e:
            logger.warning("[Bridge] Cache write failed: %s", e)

    return result


# ── SQLite Cache ─────────────────────────────────────────────────────────────


def _get_cache_db_path() -> str:
    """Return the path to the discovery cache database."""
    base_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    registry_dir = os.path.join(base_dir, "engine", "registry")
    os.makedirs(registry_dir, exist_ok=True)
    return os.path.join(registry_dir, "discovery_results.db")


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    """Create the cache table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovery_cache (
            url TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()


def _load_from_cache(target_url: str) -> Optional[DiscoveryResult]:
    """Load a cached discovery result if it exists and is less than 7 days old."""
    db_path = _get_cache_db_path()
    if not os.path.isfile(db_path):
        return None

    conn = sqlite3.connect(db_path, timeout=5)
    try:
        _ensure_cache_table(conn)
        cursor = conn.execute(
            "SELECT result_json, created_at FROM discovery_cache WHERE url = ?",
            (target_url,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        result_json, created_at = row
        # Check if cache is less than 7 days old
        age_days = (time.time() - created_at) / 86400
        if age_days > 7:
            # Cache too old, delete it
            conn.execute("DELETE FROM discovery_cache WHERE url = ?", (target_url,))
            conn.commit()
            return None

        raw = json.loads(result_json)
        return parse_result(raw)
    except Exception:
        return None
    finally:
        conn.close()


def _save_to_cache(
    target_url: Union[str, Dict[str, DiscoveryResult]],
    result: Optional[DiscoveryResult] = None,
) -> None:
    """Save one or more discovery results to the SQLite cache."""
    db_path = _get_cache_db_path()
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        _ensure_cache_table(conn)

        if isinstance(target_url, dict):
            now = time.time()
            data = [
                (url, json.dumps(res.to_gui_dict()), now)
                for url, res in target_url.items()
            ]
            conn.executemany(
                """INSERT OR REPLACE INTO discovery_cache (url, result_json, created_at)
                   VALUES (?, ?, ?)""",
                data,
            )
        else:
            if result is None:
                raise ValueError(
                    "result must be provided when saving a single target_url"
                )
            result_dict = result.to_gui_dict()
            conn.execute(
                """INSERT OR REPLACE INTO discovery_cache (url, result_json, created_at)
                   VALUES (?, ?, ?)""",
                (target_url, json.dumps(result_dict), time.time()),
            )
        conn.commit()
    finally:
        conn.close()
