"""
engine/registry/__init__.py
==============================
Registry layer — settings, captcha stats, and discovery store.
"""
from engine.registry.captcha_stats import CaptchaStatsManager

__all__ = ["CaptchaStatsManager"]
