"""
ai_captcha/captcha_dispatcher.py
===================================
Central dispatcher that routes captcha solve requests to the configured
service (CapSolver, 2captcha, AntiCaptcha) and records telemetry in
``CaptchaStatsManager`` for every attempt.
"""
from __future__ import annotations

import time
import logging
from typing import Any, Optional

from .capsolver_api import CapsolverAPI
from .twocaptcha_api import TwoCaptchaAPI
from .anticaptcha_api import AntiCaptchaAPI

logger = logging.getLogger(__name__)

# Captcha type constants
TYPE_6CHAR       = "6char_alphanum"
TYPE_TEXT        = "text_variable"
TYPE_MATH        = "math_captcha"
TYPE_IMG_SELECT  = "image_select"
TYPE_AUDIO       = "audio_captcha"
TYPE_RECAPTCHA   = "recaptcha_v2"
TYPE_HCAPTCHA    = "hcaptcha"
TYPE_AUTO        = "auto"


class CaptchaDispatcher:
    """
    Routes captcha solve requests to the active service and records
    all attempts to ``CaptchaStatsManager``.

    Parameters
    ----------
    service : str
        One of ``"capsolver"``, ``"2captcha"``, ``"anticaptcha"``.
    api_key : str
        API key for the selected service.
    """

    def __init__(self, service: str = "capsolver", api_key: str = "") -> None:
        self.service = service
        self.api_key = api_key

        if service == "capsolver":
            self.api = CapsolverAPI(api_key)
        elif service == "2captcha":
            self.api = TwoCaptchaAPI(api_key)
        elif service == "anticaptcha":
            self.api = AntiCaptchaAPI(api_key)
        else:
            logger.warning("[CaptchaDispatcher] Unknown service %r — no API client.", service)
            self.api = None

        # Lazy-import to avoid circular deps at module load time
        self._stats: Optional[Any] = None

    def _get_stats(self) -> Any:
        """Return (and cache) the CaptchaStatsManager singleton."""
        if self._stats is None:
            try:
                from engine.registry.captcha_stats import CaptchaStatsManager
                self._stats = CaptchaStatsManager()
            except Exception as exc:
                logger.warning("[CaptchaDispatcher] CaptchaStatsManager unavailable: %s", exc)
        return self._stats

    def solve(self, task_type: str, **kwargs: Any) -> Optional[str]:
        """
        Dispatch a captcha solve request to the active API and record
        the outcome in CaptchaStatsManager.

        Parameters
        ----------
        task_type : str
            One of the TYPE_* constants defined in this module.
        **kwargs
            Passed verbatim to the underlying API's ``solve()`` method.

        Returns
        -------
        str or None
            The solved captcha token/answer, or ``None`` on failure.
        """
        if not self.api:
            logger.error("[CaptchaDispatcher] No API client configured for service %r.", self.service)
            return None

        stats = self._get_stats()
        if stats:
            try:
                stats.record_request(self.service)
            except Exception:
                pass

        start = time.monotonic()
        result: Optional[str] = None
        success = False

        try:
            result = self.api.solve(task_type, **kwargs)
            success = result is not None
        except Exception as exc:
            logger.error("[CaptchaDispatcher] Solve error (%s/%s): %s", self.service, task_type, exc)
            success = False
        finally:
            elapsed = time.monotonic() - start
            if stats:
                try:
                    if success:
                        stats.record_success(self.service, elapsed)
                    else:
                        stats.record_failure(self.service, elapsed)
                except Exception:
                    pass

        return result


def get_dispatcher(service: str = "capsolver", api_key: str = "") -> CaptchaDispatcher:
    """Factory helper — returns a configured ``CaptchaDispatcher``."""
    return CaptchaDispatcher(service, api_key)
