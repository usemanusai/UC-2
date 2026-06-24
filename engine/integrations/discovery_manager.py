"""
engine/integrations/discovery_manager.py
==========================================
High-level coordinator for the AI-assisted CSS selector discovery pipeline.

Architecture
------------
``DiscoveryManager`` sits between the Tkinter GUI and the low-level
``discovery_bridge.run_and_validate`` function.  It:

1. Reads the active settings (API keys, model, proxy config) from the
   app config / GUI values passed in at call time.
2. Decides whether to use the Claude proxy or OpenRouter for the AI call.
3. Delegates to ``discovery_bridge.run_and_validate_cached`` with a
   SQLite cache so repeated runs against the same URL skip the AI call.
4. Returns a ``DiscoveryResult`` to the caller for GUI population.
5. Persists each successful result to ``engine/registry/discovery_results.db``.

Constants (environment-overridable)
------------------------------------
- ``ANTHROPIC_BASE_URL``  — default ``http://localhost:8080``
- ``CLAUDE_PROXY_MODEL``  — default ``gemini-2.0-flash``
- ``ANTHROPIC_AUTH_TOKEN``— default ``test``
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Proxy defaults ────────────────────────────────────────────────────────────
_PROXY_DEFAULT_URL   = os.getenv("ANTHROPIC_BASE_URL",   "http://localhost:8080")
_PROXY_DEFAULT_MODEL = os.getenv("CLAUDE_PROXY_MODEL",   "gemini-2.0-flash")
_PROXY_DEFAULT_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "test")

# Path to the discovery results SQLite cache
_DISCOVERY_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "registry", "discovery_results.db",
)


class DiscoveryManager:
    """
    Orchestrates the end-to-end AI selector discovery pipeline.

    Parameters
    ----------
    api_keys : list[str]
        OpenRouter API keys.  Pass an empty list to force the Claude proxy.
    preferred_model : str
        OpenRouter model identifier.  Empty string = use default.
    claude_proxy_url : str
        Base URL of the local Claude proxy server.
    claude_proxy_model : str
        Model name forwarded to the proxy.
    claude_proxy_enabled : bool
        When ``True`` the manager tries the proxy first, before OpenRouter.
    cache_db : str
        Path to the SQLite discovery cache database.
    log_callback : callable
        Function that accepts a single ``str`` to display progress messages.
    """

    def __init__(
        self,
        api_keys: Optional[List[str]] = None,
        preferred_model: str = "",
        claude_proxy_url: str = _PROXY_DEFAULT_URL,
        claude_proxy_model: str = _PROXY_DEFAULT_MODEL,
        claude_proxy_enabled: bool = False,
        cache_db: str = _DISCOVERY_DB,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.api_keys = api_keys or []
        self.preferred_model = preferred_model
        self.claude_proxy_url = claude_proxy_url
        self.claude_proxy_model = claude_proxy_model
        self.claude_proxy_enabled = claude_proxy_enabled
        self.cache_db = cache_db
        self.log_callback: Callable[[str], None] = log_callback or (lambda msg: logger.info(msg))

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, target_url: str) -> Optional[Dict[str, Any]]:
        """
        Execute the full discovery pipeline for ``target_url``.

        Returns a dict representation of ``DiscoveryResult`` on success,
        or ``None`` if discovery failed.
        """
        self.log_callback(f"[DiscoveryManager] Starting discovery for: {target_url}")

        try:
            from engine.core.discovery_bridge import run_and_validate_cached
            result = run_and_validate_cached(
                target_url=target_url,
                api_keys=self.api_keys,
                preferred_model=self.preferred_model,
                claude_proxy_url=self.claude_proxy_url,
                claude_proxy_model=self.claude_proxy_model,
                claude_proxy_enabled=self.claude_proxy_enabled,
                cache_db=self.cache_db,
                log_callback=self.log_callback,
            )
        except Exception as exc:
            self.log_callback(f"[DiscoveryManager] Discovery failed: {exc}")
            logger.error("[DiscoveryManager] Discovery pipeline error: %s", exc, exc_info=True)
            return None

        if result is None:
            self.log_callback("[DiscoveryManager] Discovery returned no result.")
            return None

        result_dict = result.model_dump() if hasattr(result, "model_dump") else vars(result)
        self._persist_result(target_url, result_dict)
        self.log_callback(
            f"[DiscoveryManager] Discovery complete — confidence: {result_dict.get('confidence', 'N/A')}"
        )
        return result_dict

    def get_cached_result(self, target_url: str) -> Optional[Dict[str, Any]]:
        """
        Return the most recent cached discovery result for ``target_url``,
        or ``None`` if no cache entry exists.
        """
        if not os.path.exists(self.cache_db):
            return None
        try:
            conn = sqlite3.connect(self.cache_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT result_json FROM discovery_cache "
                "WHERE target_url = ? ORDER BY cached_at DESC LIMIT 1",
                (target_url,),
            )
            row = cur.fetchone()
            conn.close()
            if row:
                import json
                return json.loads(row["result_json"])
        except Exception as exc:
            logger.warning("[DiscoveryManager] Cache read failed: %s", exc)
        return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _persist_result(self, target_url: str, result_dict: Dict[str, Any]) -> None:
        """Write a successful result to the persistent discovery DB."""
        import json
        try:
            os.makedirs(os.path.dirname(self.cache_db), exist_ok=True)
            conn = sqlite3.connect(self.cache_db)
            cur = conn.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS discovery_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_url TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    discovered_at REAL NOT NULL
                )"""
            )
            cur.execute(
                "INSERT INTO discovery_results (target_url, result_json, discovered_at) VALUES (?, ?, ?)",
                (target_url, json.dumps(result_dict), time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("[DiscoveryManager] Failed to persist result: %s", exc)
