"""
engine/registry/captcha_stats.py
==================================
Canonical, thread-safe singleton for tracking captcha solver statistics.

This is the SINGLE authoritative CaptchaStatsManager — used by:
- engine/registry/gui_captcha_stats.py (GUI dashboard)
- ai_captcha/captcha_dispatcher.py (wired call-site)
- engine/reporting/captcha_stats.py (re-exports this class for backward compat)

Schema (persisted to JSON)
--------------------------
{
    "total_requests": int,
    "successful_solves": int,
    "failed_solves": int,
    "service_stats": {
        "<service_name>": {
            "requests": int,
            "successes": int,
            "failures": int,
            "total_time": float
        }
    }
}
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

_DEFAULT_FILEPATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "configs", "captcha_stats.json"
)


class CaptchaStatsManager:
    """
    Thread-safe singleton for recording and persisting captcha solve statistics.

    Use via ``CaptchaStatsManager()`` — always returns the same instance.
    """

    _instance: "CaptchaStatsManager | None" = None
    _class_lock = threading.RLock()

    def __new__(cls, filepath: str = _DEFAULT_FILEPATH) -> "CaptchaStatsManager":
        with cls._class_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._filepath = filepath
                inst._stats_lock = threading.RLock()
                inst._stats: Dict[str, Any] = {
                    "total_requests": 0,
                    "successful_solves": 0,
                    "failed_solves": 0,
                    "service_stats": {},
                }
                inst._load()
                cls._instance = inst
            return cls._instance

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        with self._stats_lock:
            if os.path.exists(self._filepath):
                try:
                    with open(self._filepath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if isinstance(data, dict):
                        # Merge loaded data preserving defaults for missing keys
                        self._stats.update(data)
                        # Ensure service_stats is always a dict
                        if not isinstance(self._stats.get("service_stats"), dict):
                            self._stats["service_stats"] = {}
                except Exception as exc:
                    logger.error("[CaptchaStats] Failed to load stats: %s", exc)

    def _save(self) -> None:
        """Atomically write stats to disk."""
        with self._stats_lock:
            dir_path = os.path.dirname(self._filepath)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            tmp_path = self._filepath + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as fh:
                    json.dump(self._stats, fh, indent=4)
                os.replace(tmp_path, self._filepath)
            except Exception as exc:
                logger.error("[CaptchaStats] Failed to save stats: %s", exc)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _ensure_service(self, service: str) -> None:
        """Create a default service entry if one doesn't exist (MUST hold lock)."""
        if service not in self._stats["service_stats"]:
            self._stats["service_stats"][service] = {
                "requests": 0,
                "successes": 0,
                "failures": 0,
                "total_time": 0.0,
            }

    # ── Recording API ─────────────────────────────────────────────────────────

    def record_request(self, service: str) -> None:
        """Increment request counter for ``service``."""
        with self._stats_lock:
            self._stats["total_requests"] += 1
            self._ensure_service(service)
            self._stats["service_stats"][service]["requests"] += 1
            self._save()

    def record_success(self, service: str, duration: float = 0.0) -> None:
        """Increment success counter for ``service`` and log elapsed time."""
        with self._stats_lock:
            self._stats["successful_solves"] += 1
            self._ensure_service(service)
            svc = self._stats["service_stats"][service]
            svc["successes"] += 1
            svc["total_time"] += duration
            self._save()

    def record_failure(self, service: str, duration: float = 0.0) -> None:
        """Increment failure counter for ``service`` and log elapsed time."""
        with self._stats_lock:
            self._stats["failed_solves"] += 1
            self._ensure_service(service)
            svc = self._stats["service_stats"][service]
            svc["failures"] += 1
            svc["total_time"] += duration
            self._save()

    def record_attempt(self, service: str, success: bool, duration: float = 0.0) -> None:
        """
        Convenience method: record request + success or failure in one call.

        Compatible with the engine/reporting/captcha_stats.py API so callers
        can use either recording style.
        """
        self.record_request(service)
        if success:
            self.record_success(service, duration)
        else:
            self.record_failure(service, duration)

    # ── Query API ─────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return a deep copy of the current stats dict."""
        with self._stats_lock:
            return copy.deepcopy(self._stats)

    def reset(self) -> None:
        """Reset all counters to zero and overwrite the persisted file."""
        with self._stats_lock:
            self._stats = {
                "total_requests": 0,
                "successful_solves": 0,
                "failed_solves": 0,
                "service_stats": {},
            }
            self._save()
        logger.info("[CaptchaStats] Stats reset.")
