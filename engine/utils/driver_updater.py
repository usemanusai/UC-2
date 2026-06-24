"""
engine/utils/driver_updater.py
================================
ChromeDriver version management — checks for mismatch between the locally
installed Chrome and the cached chromedriver binary, and triggers an update
via undetected_chromedriver's internal downloader when a mismatch is detected.

This module is intentionally dependency-light.  It imports
``undetected_chromedriver`` lazily so the rest of the engine can import this
module even when uc is not installed in the current venv.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import platform
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Where UC stores its managed chromedriver binaries
_UC_DATA_DIR = Path.home() / "appdata" / "roaming" / "undetected_chromedriver" if platform.system() == "Windows" \
    else Path.home() / ".local" / "share" / "undetected_chromedriver"


class ChromeDriverUpdater:
    """
    Ensures the cached chromedriver binary matches the locally installed
    Chrome major version.

    Usage::

        updater = ChromeDriverUpdater()
        path = updater.ensure_driver()   # returns path to usable chromedriver
    """

    def __init__(self, chrome_binary: Optional[str] = None):
        self.chrome_binary = chrome_binary

    # ── Public API ────────────────────────────────────────────────────────────

    def ensure_driver(self) -> Optional[str]:
        """
        Return the path to a chromedriver binary compatible with the local Chrome.

        Steps:
        1. Detect local Chrome version.
        2. Check if the cached UC chromedriver matches.
        3. If mismatch or missing, trigger undetected_chromedriver auto-patcher.
        4. Return the final driver path.
        """
        chrome_ver = self._get_chrome_major_version()
        if not chrome_ver:
            logger.warning("[DriverUpdater] Cannot detect Chrome version; skipping driver update.")
            return None

        logger.info("[DriverUpdater] Detected Chrome major version: %s", chrome_ver)

        driver_path = self._find_existing_driver()
        if driver_path:
            cached_ver = self._get_driver_version(driver_path)
            if cached_ver and cached_ver == chrome_ver:
                logger.info(
                    "[DriverUpdater] Cached chromedriver v%s matches Chrome v%s — no update needed.",
                    cached_ver,
                    chrome_ver,
                )
                return driver_path
            logger.info(
                "[DriverUpdater] Cached chromedriver v%s != Chrome v%s — updating...",
                cached_ver,
                chrome_ver,
            )

        return self._patch_driver(chrome_ver)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_chrome_major_version(self) -> Optional[str]:
        binary = self.chrome_binary
        if not binary:
            from engine.utils.driver_config import _find_chrome_binary
            binary = _find_chrome_binary()
        if not binary:
            return None
        try:
            system = platform.system().lower()
            if system == "windows":
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"(Get-Item '{binary}').VersionInfo.ProductVersion"],
                    capture_output=True, text=True, timeout=10,
                )
                raw = result.stdout.strip()
            else:
                result = subprocess.run(
                    [binary, "--version"],
                    capture_output=True, text=True, timeout=10,
                )
                raw = result.stdout.strip()
            match = re.search(r"(\d+)\.\d+\.\d+", raw)
            return match.group(1) if match else None
        except Exception as exc:
            logger.warning("[DriverUpdater] Failed to read Chrome version: %s", exc)
            return None

    def _find_existing_driver(self) -> Optional[str]:
        """Look for an existing undetected_chromedriver binary."""
        exe_name = "chromedriver.exe" if platform.system() == "Windows" else "chromedriver"
        candidate = _UC_DATA_DIR / exe_name
        if candidate.is_file():
            return str(candidate)
        # Also check PATH
        try:
            result = subprocess.run(
                ["where", exe_name] if platform.system() == "Windows" else ["which", exe_name],
                capture_output=True, text=True, timeout=5,
            )
            path = result.stdout.strip().splitlines()[0].strip() if result.returncode == 0 else ""
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
        return None

    def _get_driver_version(self, driver_path: str) -> Optional[str]:
        try:
            result = subprocess.run(
                [driver_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            match = re.search(r"ChromeDriver (\d+)\.\d+\.\d+", result.stdout)
            return match.group(1) if match else None
        except Exception:
            return None

    def _patch_driver(self, chrome_major: str) -> Optional[str]:
        """Use undetected_chromedriver's patcher to download the correct driver."""
        try:
            import undetected_chromedriver as uc
            patcher = uc.Patcher(version_main=int(chrome_major))
            patcher.auto()
            driver_path = patcher.executable_path
            logger.info("[DriverUpdater] Patched chromedriver available at: %s", driver_path)
            return driver_path
        except Exception as exc:
            logger.error("[DriverUpdater] Driver patch failed: %s", exc)
            return None
