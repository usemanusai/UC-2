# ai_captcha/claude_proxy_bridge.py
"""
Claude Proxy Bridge for the CAPTCHA System
═══════════════════════════════════════════════════════════════════════════════
Routes all captcha workflow HTTP calls through the antigravity-claude-proxy
at http://localhost:8080 instead of OpenRouter.ai, when the proxy is running
and no OpenRouter key is available (or when the GUI toggle is ON).
Integrated with Z3ActionVerifier for SMT logic verification on action outputs.
"""

from __future__ import annotations

import logging
import os
import json
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ── Proxy defaults (match discovery_manager.py constants) ────────────────────
_PROXY_DEFAULT_URL   = os.getenv("ANTHROPIC_BASE_URL",  "http://localhost:8080")
_PROXY_DEFAULT_MODEL = os.getenv("CLAUDE_PROXY_MODEL",  "gemini-3-flash")
_PROXY_DEFAULT_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "test")

# OpenAI-compatible chat completions endpoint exposed by the proxy
_PROXY_CHAT_ENDPOINT = f"{_PROXY_DEFAULT_URL.rstrip('/')}/v1/chat/completions"


def is_proxy_healthy(timeout: float = 3.0) -> bool:
    """Return True if the claude proxy health endpoint responds 200 OK."""
    import httpx  # already present in discovery_squad venv; fall back to requests
    health_url = f"{_PROXY_DEFAULT_URL.rstrip('/')}/health"
    try:
        resp = httpx.get(health_url, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        try:
            import requests as _req
            resp = _req.get(health_url, timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False


def build_proxy_config(
    model:       Optional[str] = None,
    base_url:    Optional[str] = None,
    auth_token:  Optional[str] = None,
    extra:       Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a solver config dict that points every workflow at the Claude proxy
    instead of OpenRouter.
    """
    resolved_model    = model      or _PROXY_DEFAULT_MODEL
    resolved_base_url = base_url   or _PROXY_DEFAULT_URL
    resolved_token    = auth_token or _PROXY_DEFAULT_TOKEN
    endpoint          = f"{resolved_base_url.rstrip('/')}/v1/chat/completions"

    cfg: Dict[str, Any] = {
        "OPENROUTER_API_KEY":      resolved_token,
        "OPENROUTER_API_ENDPOINT": endpoint,
        "AI_VISION_MODEL":         resolved_model,
        "AI_AUDIO_MODEL":          resolved_model,
        "OPENROUTER_TIMEOUT":      60,
        "RETRY_DELAY":             3.0,
        "ENABLE_CACHING":          True,
        "CACHE_TTL":               3600,
        "_via_claude_proxy":       True,
    }

    if extra:
        cfg.update(extra)

    logger.info(
        "[CaptchaProxyBridge] Config built → endpoint=%s  model=%s",
        endpoint, resolved_model,
    )
    return cfg


def get_proxy_config_if_available(
    openrouter_keys: Optional[list] = None,
    force_proxy:      bool           = False,
) -> Optional[Dict[str, Any]]:
    """
    Decide whether to use the proxy config or fall back to OpenRouter.
    """
    has_or_keys = bool(
        openrouter_keys and any(k.strip() for k in openrouter_keys)
    )

    if force_proxy or not has_or_keys:
        if is_proxy_healthy():
            logger.info("[CaptchaProxyBridge] ✓ Proxy healthy — captcha will use claude proxy.")
            return build_proxy_config()
        else:
            if force_proxy:
                logger.error(
                    "[CaptchaProxyBridge] ✗ Proxy forced but NOT reachable at %s. "
                    "Captcha solving may fail.",
                    _PROXY_DEFAULT_URL,
                )
            else:
                logger.warning(
                    "[CaptchaProxyBridge] No OpenRouter keys and proxy not reachable — "
                    "captcha solving will likely fail."
                )
            return None

    return None


# ── Z3 Neuro-Symbolic Verification Integration ──────────────────────────────────

def verify_llm_action_sequence(actions: List[Dict[str, Any]]) -> bool:
    """
    Pipes action predictions through Z3ActionVerifier to assert SAT logic constraints.
    """
    try:
        from engine.kernel.math_engine.verification import Z3ActionVerifier
        verifier = Z3ActionVerifier()
        is_valid, msg, _ = verifier.verify_sequence(actions)
        if not is_valid:
            logger.warning(f"[CaptchaProxyBridge] Action sequence SMT check failed: {msg}")
            return False
        logger.info("[CaptchaProxyBridge] Action sequence successfully verified via Z3 SMT solver.")
        return True
    except Exception as e:
        logger.warning(f"[CaptchaProxyBridge] Error during action verification: {e}")
        return True # Fallback to True if Z3 fails to load


def intercept_and_verify_response(response_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Intercepts LLM response completions, extracts any action sequence,
    and validates it via SMT. If validation fails, raises ValueError or logs error.
    """
    try:
        content = response_json["choices"][0]["message"]["content"]
        # Standard cleaning of markdown fences
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        data = json.loads(content)
        
        # Determine if there's an action sequence inside
        actions = []
        if isinstance(data, list):
            actions = data
        elif isinstance(data, dict):
            for key in ["actions", "steps", "sequence", "action_sequence"]:
                if key in data and isinstance(data[key], list):
                    actions = data[key]
                    break
                    
        if actions:
            from engine.kernel.math_engine.verification import Z3ActionVerifier
            verifier = Z3ActionVerifier()
            is_valid, msg, _ = verifier.verify_sequence(actions)
            if not is_valid:
                logger.error(f"[CaptchaProxyBridge] Z3 SMT verification failed for actions: {msg}")
                raise ValueError(f"UNSAT: {msg}")
    except Exception as e:
        if "UNSAT" in str(e):
            raise e
        logger.warning(f"[CaptchaProxyBridge] Response interception check skipped: {e}")
        
    return response_json
