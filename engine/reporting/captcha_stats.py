"""
engine/reporting/captcha_stats.py
====================================
Backward-compatibility re-export.

The canonical ``CaptchaStatsManager`` lives in
``engine.registry.captcha_stats``.  This module re-exports it so any
existing import path continues to work without creating a second, conflicting
singleton.
"""
from engine.registry.captcha_stats import CaptchaStatsManager  # noqa: F401

__all__ = ["CaptchaStatsManager"]
