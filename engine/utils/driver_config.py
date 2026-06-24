"""
engine/utils/driver_config.py
==============================
Centralised ChromeDriver / Chrome binary path resolution and version
pinning logic for the UC browser kernel.

Responsibilities:
- Locate the locally installed Chrome binary (registry on Windows, PATH on POSIX).
- Resolve the matching ChromeDriver version from the local or cached manifest.
- Expose a single ``DriverConfig`` dataclass consumed by ``BrowserFactory``.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Default Chrome binary search paths by OS ──────────────────────────────────
_WIN_CHROME_PATHS: list[str] = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
]

_POSIX_CHROME_NAMES: list[str] = [
    "google-chrome",
    "google-chrome-stable",
    "chromium-browser",
    "chromium",
]


@dataclass
class DriverConfig:
    """
    Resolved configuration for a single ChromeDriver session.

    Attributes
    ----------
    chrome_binary : str or None
        Absolute path to the Chrome executable.  ``None`` means "let
        undetected_chromedriver locate it automatically".
    chromedriver_path : str or None
        Absolute path to the chromedriver binary.  ``None`` means "auto-download
        via undetected_chromedriver".
    chrome_version : str or None
        Major Chrome version string (e.g. ``"124"``).  Used to select the
        matching driver version.
    debug_port : int
        Remote debugging port to attach to.  0 = auto-assigned.
    headless : bool
        Whether to launch Chrome in headless mode.
    extra_arguments : list[str]
        Additional ``--flag`` strings passed verbatim to Chrome at launch.
    """

    chrome_binary: Optional[str] = None
    chromedriver_path: Optional[str] = None
    chrome_version: Optional[str] = None
    debug_port: int = 0
    headless: bool = False
    extra_arguments: list[str] = field(default_factory=list)

    # ── Factory helpers ──────────────────────────────────────────────────────

    @classmethod
    def auto_detect(
        cls,
        headless: bool = False,
        debug_port: int = 0,
        extra_arguments: Optional[list[str]] = None,
    ) -> "DriverConfig":
        """
        Build a ``DriverConfig`` by auto-detecting the local Chrome installation.

        Returns a config with ``chrome_binary`` and ``chrome_version`` filled in
        if detection succeeds, or ``None`` values if Chrome cannot be found (in
        which case undetected_chromedriver will try its own detection).
        """
        binary = _find_chrome_binary()
        version = _get_chrome_version(binary) if binary else None

        cfg = cls(
            chrome_binary=binary,
            chrome_version=version,
            headless=headless,
            debug_port=debug_port,
            extra_arguments=extra_arguments or [],
        )
        logger.info(
            "[DriverConfig] Auto-detected → binary=%s  version=%s  headless=%s",
            binary,
            version,
            headless,
        )
        return cfg


# ── Internal helpers ──────────────────────────────────────────────────────────


def _find_chrome_binary() -> Optional[str]:
    """Return the first Chrome executable found on this system, or ``None``."""
    system = platform.system().lower()

    if system == "windows":
        for path in _WIN_CHROME_PATHS:
            if os.path.isfile(path):
                logger.debug("[DriverConfig] Found Chrome at %s", path)
                return path
        # Try registry
        try:
            import winreg  # type: ignore
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Google\Chrome\BLBeacon",
            )
            chrome_path, _ = winreg.QueryValueEx(key, "path")
            if os.path.isfile(chrome_path):
                return chrome_path
        except Exception:
            pass
    else:
        for name in _POSIX_CHROME_NAMES:
            try:
                result = subprocess.run(
                    ["which", name],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                path = result.stdout.strip()
                if path and os.path.isfile(path):
                    logger.debug("[DriverConfig] Found Chrome at %s", path)
                    return path
            except Exception:
                continue

    logger.warning("[DriverConfig] Chrome binary not found; undetected_chromedriver will auto-locate.")
    return None


def _get_chrome_version(chrome_binary: str) -> Optional[str]:
    """Extract the major version number from the Chrome binary."""
    try:
        system = platform.system().lower()
        if system == "windows":
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Item '{chrome_binary}').VersionInfo.ProductVersion",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            raw = result.stdout.strip()
        else:
            result = subprocess.run(
                [chrome_binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            raw = result.stdout.strip()

        match = re.search(r"(\d+)\.\d+\.\d+", raw)
        if match:
            major = match.group(1)
            logger.debug("[DriverConfig] Detected Chrome major version: %s", major)
            return major
    except Exception as exc:
        logger.warning("[DriverConfig] Could not determine Chrome version: %s", exc)
    return None
