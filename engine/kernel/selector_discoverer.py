"""
engine/kernel/selector_discoverer.py
======================================
CDP-connected AI self-discovery engine.

This module provides ``SelectorDiscoverer``, which attaches to a running
Chrome instance via the Chrome DevTools Protocol (CDP) and uses the live
DOM snapshot (rather than static HTML) to discover form selector candidates.

This is the **third tier** of the fallback chain:
  1. Explicit GUI config
  2. Heuristic dictionary (heuristics.py)
  3. CDP + AI discovery  ← this module

Because it requires a live Chrome session, it is only invoked after tiers
1 and 2 have failed.

Dependencies
------------
- ``websocket-client`` (already required by undetected_chromedriver)
- ``requests``

The module does NOT import Selenium or undetected_chromedriver directly;
it communicates with Chrome over raw CDP WebSocket, making it lighter.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ── CSS selector stability ranking ───────────────────────────────────────────
# Higher = more stable/preferred
_STABILITY_RANK: Dict[str, int] = {
    "id": 100,
    "name": 90,
    "data-testid": 85,
    "data-cy": 84,
    "data-qa": 83,
    "aria-label": 70,
    "class": 40,
    "xpath": 10,
}

# Dynamic class patterns to reject (hash-like, auto-generated)
_DYNAMIC_CLASS_RE = re.compile(
    r"(?:[a-f0-9]{6,}|_[a-z0-9]{4,}|-[a-z0-9]{5,}|__[a-zA-Z0-9]{4,})",
    re.IGNORECASE,
)

# Minimum confidence threshold to accept a discovered selector
_MIN_CONFIDENCE = 0.65


class SelectorDiscoverer:
    """
    Discovers CSS selectors for a login form by querying the live DOM via CDP.

    Parameters
    ----------
    debug_port : int
        Chrome's remote debugging port (``--remote-debugging-port=XXXX``).
    ai_client : OpenRouterClient or None
        Pre-configured AI client for sending DOM snapshots to the LLM.
        When ``None``, the discoverer operates in heuristic-only mode.
    log_callback : callable
        Progress logger callable.
    """

    def __init__(
        self,
        debug_port: int,
        ai_client: Optional[Any] = None,
        log_callback: Optional[Any] = None,
    ):
        self.debug_port = debug_port
        self.ai_client = ai_client
        self.log_callback = log_callback or (lambda msg: logger.info(msg))

    # ── Public API ────────────────────────────────────────────────────────────

    def discover(self, target_url: str) -> Optional[Dict[str, str]]:
        """
        Run the full CDP + AI discovery flow on the page currently loaded in
        Chrome at ``self.debug_port``.

        Returns a dict with keys ``username``, ``password``, ``submit``
        (each being a CSS selector string), or ``None`` if discovery fails.
        """
        self.log_callback(f"[SelectorDiscoverer] Starting CDP discovery on port {self.debug_port}")

        tab_id, ws_url = self._get_active_tab_info()
        if not ws_url:
            self.log_callback("[SelectorDiscoverer] Cannot find active CDP tab.")
            return None

        dom_elements = self._extract_dom_via_cdp(ws_url)
        if not dom_elements:
            self.log_callback("[SelectorDiscoverer] DOM extraction returned no elements.")
            return None

        self.log_callback(f"[SelectorDiscoverer] Extracted {len(dom_elements)} DOM elements.")

        # Phase 1: Heuristic scoring (no AI required)
        heuristic_result = self._heuristic_score(dom_elements)
        if self._result_is_confident(heuristic_result):
            self.log_callback("[SelectorDiscoverer] Heuristic discovery sufficient.")
            return heuristic_result

        # Phase 2: AI-assisted discovery (requires OpenRouterClient)
        if self.ai_client:
            self.log_callback("[SelectorDiscoverer] Escalating to AI analysis...")
            ai_result = self._ai_discover(dom_elements, target_url)
            if ai_result:
                return ai_result

        self.log_callback("[SelectorDiscoverer] Discovery incomplete — returning best heuristic guess.")
        return heuristic_result if any(heuristic_result.values()) else None

    # ── CDP communication ─────────────────────────────────────────────────────

    def _get_active_tab_info(self) -> Tuple[Optional[str], Optional[str]]:
        """Query the CDP /json endpoint for the active page tab."""
        try:
            resp = requests.get(
                f"http://localhost:{self.debug_port}/json",
                timeout=5,
            )
            tabs = resp.json()
            for tab in tabs:
                if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                    return tab.get("id"), tab["webSocketDebuggerUrl"]
        except Exception as exc:
            logger.warning("[SelectorDiscoverer] CDP tab list failed: %s", exc)
        return None, None

    def _extract_dom_via_cdp(self, ws_url: str) -> List[Dict[str, Any]]:
        """
        Connect to the CDP WebSocket and evaluate JS to extract all form
        input elements from the live DOM.
        """
        try:
            import websocket  # type: ignore
        except ImportError:
            logger.error("[SelectorDiscoverer] websocket-client not installed.")
            return []

        js_snippet = """
        (function() {
            var results = [];
            var inputs = document.querySelectorAll('input, button, [role="button"], a[class*="btn"]');
            inputs.forEach(function(el) {
                results.push({
                    tag: el.tagName,
                    id: el.id || null,
                    name: el.name || null,
                    type: el.type || null,
                    placeholder: el.placeholder || null,
                    className: el.className || null,
                    ariaLabel: el.getAttribute('aria-label') || null,
                    dataTestId: el.getAttribute('data-testid') || null,
                    dataCy: el.getAttribute('data-cy') || null,
                    visible: el.offsetParent !== null,
                    rect: JSON.stringify(el.getBoundingClientRect())
                });
            });
            return JSON.stringify(results);
        })();
        """

        ws = None
        try:
            ws = websocket.create_connection(ws_url, timeout=10)
            msg_id = int(time.time() * 1000)
            ws.send(json.dumps({
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {"expression": js_snippet, "returnByValue": True},
            }))
            raw = ws.recv()
            result = json.loads(raw)
            value_str = result.get("result", {}).get("result", {}).get("value", "[]")
            elements: List[Dict[str, Any]] = json.loads(value_str)
            return elements
        except Exception as exc:
            logger.warning("[SelectorDiscoverer] CDP DOM extraction failed: %s", exc)
            return []
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

    # ── Heuristic scoring ─────────────────────────────────────────────────────

    def _heuristic_score(self, elements: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Score candidate elements using the stability ranking table and return
        the best CSS selectors for username, password, and submit.
        """
        username_candidates: List[Tuple[int, str]] = []
        password_candidates: List[Tuple[int, str]] = []
        submit_candidates: List[Tuple[int, str]] = []

        for el in elements:
            if not el.get("visible", True):
                continue

            tag = (el.get("tag") or "").upper()
            el_type = (el.get("type") or "").lower()
            selector, score = self._best_selector(el)
            if not selector:
                continue

            # Classify
            if tag == "INPUT":
                if el_type in ("password",):
                    password_candidates.append((score, selector))
                elif el_type in ("email", "text", "tel") or (
                    not el_type and any(
                        kw in (el.get("name") or "").lower() or kw in (el.get("placeholder") or "").lower()
                        for kw in ("email", "user", "login", "account", "username")
                    )
                ):
                    username_candidates.append((score, selector))
            elif tag in ("BUTTON", "A"):
                if el_type in ("submit",) or any(
                    kw in (el.get("ariaLabel") or "").lower()
                    for kw in ("sign in", "log in", "login", "submit", "continue")
                ):
                    submit_candidates.append((score, selector))

        def best(candidates: List[Tuple[int, str]]) -> str:
            return max(candidates, key=lambda x: x[0])[1] if candidates else ""

        return {
            "username": best(username_candidates),
            "password": best(password_candidates),
            "submit": best(submit_candidates),
        }

    def _best_selector(self, el: Dict[str, Any]) -> Tuple[str, int]:
        """Return the highest-stability CSS selector for an element."""
        if el.get("id") and not _DYNAMIC_CLASS_RE.search(el["id"]):
            return f"#{el['id']}", _STABILITY_RANK["id"]
        if el.get("dataTestId"):
            return f"[data-testid='{el['dataTestId']}']", _STABILITY_RANK["data-testid"]
        if el.get("dataCy"):
            return f"[data-cy='{el['dataCy']}']", _STABILITY_RANK["data-cy"]
        if el.get("name"):
            tag = (el.get("tag") or "input").lower()
            return f"{tag}[name='{el['name']}']", _STABILITY_RANK["name"]
        if el.get("ariaLabel"):
            tag = (el.get("tag") or "input").lower()
            return f"{tag}[aria-label='{el['ariaLabel']}']", _STABILITY_RANK["aria-label"]
        if el.get("className"):
            classes = el["className"].split()
            stable = [c for c in classes if not _DYNAMIC_CLASS_RE.search(c)]
            if stable:
                tag = (el.get("tag") or "input").lower()
                return f"{tag}.{stable[0]}", _STABILITY_RANK["class"]
        return "", 0

    def _result_is_confident(self, result: Dict[str, str]) -> bool:
        """True if we have at least username + password selectors."""
        return bool(result.get("username")) and bool(result.get("password"))

    # ── AI-assisted discovery ─────────────────────────────────────────────────

    def _ai_discover(
        self,
        elements: List[Dict[str, Any]],
        target_url: str,
    ) -> Optional[Dict[str, str]]:
        """Send DOM snapshot to OpenRouter AI and parse the selector response."""
        prompt = (
            f"You are analyzing a login form on {target_url}.\n"
            f"Here are the DOM elements extracted from the live page:\n"
            f"{json.dumps(elements[:60], indent=2)}\n\n"
            "Identify the best CSS selectors for:\n"
            "1. The username / email input field\n"
            "2. The password input field\n"
            "3. The submit / sign-in button\n\n"
            "Prefer stable selectors in this order: id > name > data-testid > aria-label > class.\n"
            "Reject any selector with hash-like class names or numeric suffixes.\n"
            "Return ONLY a JSON object with keys: username, password, submit.\n"
            "Example: {\"username\": \"input[name='email']\", \"password\": \"input[type='password']\", \"submit\": \"button[type='submit']\"}"
        )
        try:
            result = self.ai_client.chat_json(prompt, temperature=0.05)
            selectors = {
                "username": str(result.get("username") or ""),
                "password": str(result.get("password") or ""),
                "submit": str(result.get("submit") or ""),
            }
            self.log_callback(
                f"[SelectorDiscoverer] AI result: {selectors}"
            )
            return selectors if any(selectors.values()) else None
        except Exception as exc:
            logger.warning("[SelectorDiscoverer] AI discovery failed: %s", exc)
            return None
