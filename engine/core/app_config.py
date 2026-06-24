"""
engine/core/app_config.py
===========================
Central application configuration registry.

Provides a single ``AppConfig`` dataclass that is loaded once at startup
from the active settings manager and cached as a module-level singleton.
All subsystems (browser_factory, discovery_bridge, captcha_dispatcher) read
from this object instead of querying settings individually.

Usage
-----
    from engine.core.app_config import get_config, reload_config

    cfg = get_config()
    print(cfg.openrouter_keys)
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_config: Optional["AppConfig"] = None


@dataclass
class AppConfig:
    """
    Runtime configuration snapshot.

    All fields have safe defaults so the application can start even when no
    settings file exists yet.
    """
    # --- AI / LLM ---
    openrouter_keys: List[str] = field(default_factory=list)
    preferred_model: str = "google/gemini-2.0-flash-lite:free"
    claude_proxy_enabled: bool = False
    claude_proxy_url: str = "http://localhost:8080"
    claude_proxy_model: str = "gemini-2.0-flash"

    # --- Browser ---
    headless: bool = False
    thread_count: int = 1
    debug_port_base: int = 9222
    chrome_binary: Optional[str] = None
    chromedriver_path: Optional[str] = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    extensions_dir: str = "_ext_unpacked"

    # --- Captcha ---
    captcha_service: str = "capsolver"
    captcha_api_key: str = ""
    use_rekt_captcha: bool = True

    # --- Proxy ---
    proxy_mode: str = "No Proxy"
    proxy_list: List[str] = field(default_factory=list)
    proxy_source_url: str = ""
    proxy_update_interval: int = 300

    # --- Session ---
    temp_sessions_dir: str = "temp_sessions"
    session_max_age: int = 3600
    session_cleanup_interval: int = 300

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Output ---
    output_dir: str = "results"


def get_config() -> AppConfig:
    """Return the active ``AppConfig``, loading defaults if not yet initialised."""
    global _config
    with _lock:
        if _config is None:
            _config = _load_from_settings()
        return _config


def reload_config() -> AppConfig:
    """Force a reload from the settings manager and return the new config."""
    global _config
    with _lock:
        _config = _load_from_settings()
        return _config


def set_config(cfg: AppConfig) -> None:
    """Inject a pre-built config (used in tests or GUI live-reload)."""
    global _config
    with _lock:
        _config = cfg


# ── Internal ──────────────────────────────────────────────────────────────────

def _load_from_settings() -> AppConfig:
    """
    Attempt to read values from the settings manager (modern_settings.py).
    Falls back gracefully to defaults if the settings layer is unavailable.
    """
    cfg = AppConfig()
    try:
        from engine.registry.settings_manager import SettingsManager  # type: ignore
        sm = SettingsManager()
        settings = sm.get_all()

        cfg.openrouter_keys = _to_list(settings.get("openrouter_api_keys", ""))
        cfg.preferred_model = settings.get("preferred_model", cfg.preferred_model) or cfg.preferred_model
        cfg.claude_proxy_enabled = bool(settings.get("claude_proxy_enabled", False))
        cfg.claude_proxy_url = settings.get("claude_proxy_url", cfg.claude_proxy_url) or cfg.claude_proxy_url
        cfg.claude_proxy_model = settings.get("claude_proxy_model", cfg.claude_proxy_model) or cfg.claude_proxy_model

        cfg.headless = bool(settings.get("headless", False))
        cfg.thread_count = int(settings.get("thread_count", 1))
        cfg.chrome_binary = settings.get("chrome_binary") or None
        cfg.user_agent = settings.get("user_agent", cfg.user_agent) or cfg.user_agent

        cfg.captcha_service = settings.get("captcha_service", cfg.captcha_service) or cfg.captcha_service
        cfg.captcha_api_key = settings.get("captcha_api_key", "") or ""

        cfg.proxy_mode = settings.get("proxy_mode", cfg.proxy_mode) or cfg.proxy_mode
        raw_proxies = settings.get("proxy_list", "")
        cfg.proxy_list = _to_list(raw_proxies)

        cfg.telegram_bot_token = settings.get("telegram_bot_token", "") or ""
        cfg.telegram_chat_id = settings.get("telegram_chat_id", "") or ""
        cfg.output_dir = settings.get("output_dir", cfg.output_dir) or cfg.output_dir

        logger.info("[AppConfig] Loaded configuration from SettingsManager.")
    except Exception as exc:
        logger.warning(
            "[AppConfig] Could not load settings (%s); using defaults.", exc
        )
    return cfg


def _to_list(value: object) -> List[str]:
    """Convert a newline/comma-delimited string (or list) to a clean list of strings."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not value:
        return []
    raw = str(value)
    if "\n" in raw:
        return [s.strip() for s in raw.splitlines() if s.strip()]
    if "," in raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    stripped = raw.strip()
    return [stripped] if stripped else []
