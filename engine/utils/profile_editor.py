"""
engine/utils/profile_editor.py
================================
Chrome user-data-dir ``Preferences`` file pre-seeder.

Before launching Chrome on a fresh temp profile, UC must write the
``Preferences`` JSON file so Chrome boots with:
- Extensions pre-enabled (rektCaptcha, Moodle solver, Shaparak solver)
- Toolbar pins configured
- Notification pop-ups and sign-in prompts suppressed
- Custom UA override disabled (we handle UA via undetected_chromedriver flags)

This module operates entirely on the filesystem — no running Chrome required.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Default Preferences template ─────────────────────────────────────────────
_BASE_PREFS: Dict[str, Any] = {
    "profile": {
        "default_content_setting_values": {
            "notifications": 2,   # Block all notification prompts
            "popups": 2,          # Block all pop-up windows
        },
        "password_manager_enabled": False,
        "credentials_enable_service": False,
    },
    "browser": {
        "check_default_browser": False,
        "show_toolbar_button": False,
    },
    "signin": {
        "allowed": False,
    },
    "translate": {
        "enabled": False,
    },
    "session": {
        "restore_on_startup": 1,  # Open NTP (suppresses restore dialog)
    },
    "extensions": {
        "ui": {
            "developer_mode": False,
        },
    },
}


class ProfileEditor:
    """
    Writes and manages Chrome Preferences files for isolated session profiles.

    Parameters
    ----------
    profile_dir : str or Path
        Path to the Chrome user-data directory (the one passed as
        ``--user-data-dir`` to Chrome).
    extension_ids : list[str], optional
        Chrome extension IDs that should be allowed/pinned in the toolbar.
        These are written into the ``extensions.toolbar`` list.
    """

    def __init__(
        self,
        profile_dir: str | Path,
        extension_ids: Optional[List[str]] = None,
    ):
        self.profile_dir = Path(profile_dir)
        self.extension_ids = extension_ids or []

    # ── Public API ────────────────────────────────────────────────────────────

    def seed(self) -> bool:
        """
        Write the ``Default/Preferences`` file into the profile directory.

        Creates the ``Default/`` subdirectory if it doesn't exist.
        Always writes fresh (overwriting any stale prefs from a previous run).

        Returns ``True`` on success, ``False`` on failure.
        """
        default_dir = self.profile_dir / "Default"
        default_dir.mkdir(parents=True, exist_ok=True)
        prefs_path = default_dir / "Preferences"

        prefs = dict(_BASE_PREFS)

        # Merge in extension toolbar pins
        if self.extension_ids:
            prefs["extensions"]["pinned_extensions"] = self.extension_ids
            prefs["extensions"]["toolbar"] = self.extension_ids

        try:
            # Atomic write: write to temp then rename
            tmp_path = prefs_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
            tmp_path.replace(prefs_path)
            logger.info(
                "[ProfileEditor] Seeded Preferences at %s (extensions=%s)",
                prefs_path,
                self.extension_ids,
            )
            return True
        except Exception as exc:
            logger.error("[ProfileEditor] Failed to write Preferences: %s", exc)
            return False

    def clear(self) -> bool:
        """
        Remove the entire profile directory.

        Used during post-session cleanup to avoid stale profile bleed.
        Returns ``True`` if the directory was removed or didn't exist.
        """
        if not self.profile_dir.exists():
            return True
        try:
            shutil.rmtree(self.profile_dir, ignore_errors=True)
            logger.info("[ProfileEditor] Cleared profile dir: %s", self.profile_dir)
            return True
        except Exception as exc:
            logger.warning("[ProfileEditor] Failed to clear profile dir %s: %s", self.profile_dir, exc)
            return False

    @staticmethod
    def merge_prefs(
        profile_dir: str | Path,
        extra: Dict[str, Any],
    ) -> bool:
        """
        Deep-merge ``extra`` dict into an existing Preferences file.

        Safe to call on a live profile that Chrome is NOT currently holding open.
        """
        prefs_path = Path(profile_dir) / "Default" / "Preferences"
        if not prefs_path.exists():
            logger.warning("[ProfileEditor] Preferences file not found at %s; skipping merge.", prefs_path)
            return False

        try:
            with prefs_path.open("r", encoding="utf-8") as fh:
                existing: Dict[str, Any] = json.load(fh)

            _deep_merge(existing, extra)

            tmp = prefs_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            tmp.replace(prefs_path)
            logger.info("[ProfileEditor] Merged extra prefs into %s", prefs_path)
            return True
        except Exception as exc:
            logger.error("[ProfileEditor] Prefs merge failed: %s", exc)
            return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    """Recursively merge ``overlay`` into ``base`` in-place."""
    for key, val in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
