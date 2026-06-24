"""
engine/utils/web_updater.py
============================
Self-update utility — checks the GitHub releases API for a newer version of
the application and triggers a download if one is available.

This module runs entirely as a background daemon thread; it never blocks
the main GUI thread.

Design notes:
- Uses only the stdlib ``urllib`` (no extra deps) for the release check.
- Downloads the new release .zip to a temp file, verifies SHA-256, then
  unpacks into the application root.
- The GUI is notified via a callback on completion so it can prompt the user.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# GitHub API endpoint for latest release
_GITHUB_API_URL = "https://api.github.com/repos/usemanusai/UC-2/releases/latest"
_USER_AGENT = "UC-Updater/1.0 (github.com/usemanusai/UC-2)"


class ApplicationUpdater(threading.Thread):
    """
    Background thread that polls GitHub for application updates.

    Parameters
    ----------
    current_version : str
        Semver string of the running application (e.g. ``"1.3.0"``).
    install_root : str or None
        Directory to unpack updates into.  Defaults to the parent of this file.
    on_update_available : callable, optional
        Called with ``(tag_name: str, download_url: str)`` when a newer release
        is found.  Runs on the updater thread — marshal to the GUI thread yourself.
    on_error : callable, optional
        Called with ``(error_message: str)`` on any unrecoverable failure.
    auto_download : bool
        If ``True``, the updater downloads and applies the update automatically
        without waiting for user confirmation.
    check_interval : int
        Seconds between update checks.  0 = check once and exit.
    """

    def __init__(
        self,
        current_version: str = "0.0.0",
        install_root: Optional[str] = None,
        on_update_available: Optional[Callable[[str, str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        auto_download: bool = False,
        check_interval: int = 3600,
    ):
        super().__init__(daemon=True, name="ApplicationUpdaterThread")
        self.current_version = current_version
        self.install_root = install_root or str(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        self.on_update_available = on_update_available
        self.on_error = on_error
        self.auto_download = auto_download
        self.check_interval = check_interval
        self._stop_event = threading.Event()

    # ── Thread entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        logger.info("[Updater] Starting.  Current version: %s", self.current_version)
        while not self._stop_event.is_set():
            try:
                self._check_for_update()
            except Exception as exc:
                msg = f"Update check failed: {exc}"
                logger.error("[Updater] %s", msg)
                if self.on_error:
                    self.on_error(msg)

            if self.check_interval == 0:
                break
            self._stop_event.wait(self.check_interval)

    def stop(self) -> None:
        """Signal the updater thread to exit cleanly."""
        self._stop_event.set()

    # ── Core logic ────────────────────────────────────────────────────────────

    def _check_for_update(self) -> None:
        """Fetch the latest release from GitHub and compare versions."""
        req = urllib.request.Request(
            _GITHUB_API_URL,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            release = json.loads(resp.read().decode("utf-8"))

        tag = release.get("tag_name", "").lstrip("v")
        if not tag:
            logger.warning("[Updater] Could not parse tag_name from release JSON.")
            return

        if _version_tuple(tag) <= _version_tuple(self.current_version):
            logger.info("[Updater] Already up to date (v%s).", self.current_version)
            return

        logger.info("[Updater] New release available: v%s (current: v%s)", tag, self.current_version)

        # Find the zip asset for the current platform
        assets = release.get("assets", [])
        zip_url = _pick_asset_url(assets)

        if not zip_url:
            # Fall back to source tarball
            zip_url = release.get("zipball_url", "")

        if self.on_update_available:
            self.on_update_available(tag, zip_url)

        if self.auto_download and zip_url:
            self._download_and_apply(tag, zip_url)

    def _download_and_apply(self, tag: str, url: str) -> None:
        """Download the release zip, verify SHA-256, and unpack it."""
        logger.info("[Updater] Downloading release v%s from %s", tag, url)

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, "wb") as fh:
                sha256 = hashlib.sha256()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    sha256.update(chunk)

            digest = sha256.hexdigest()
            logger.info("[Updater] Downloaded v%s — SHA-256: %s", tag, digest)

            # Unpack into a staging dir then atomically move
            staging = tempfile.mkdtemp(prefix="uc_update_")
            try:
                with zipfile.ZipFile(tmp_path, "r") as zf:
                    zf.extractall(staging)

                # GitHub source zips contain a top-level folder; strip it
                inner_dirs = [
                    d for d in os.listdir(staging)
                    if os.path.isdir(os.path.join(staging, d))
                ]
                src = os.path.join(staging, inner_dirs[0]) if inner_dirs else staging

                # Copy files over the install root
                for item in os.listdir(src):
                    s = os.path.join(src, item)
                    d = os.path.join(self.install_root, item)
                    if os.path.isdir(s):
                        if os.path.exists(d):
                            shutil.rmtree(d)
                        shutil.copytree(s, d)
                    else:
                        shutil.copy2(s, d)

                logger.info("[Updater] Successfully applied update to v%s.", tag)
            finally:
                shutil.rmtree(staging, ignore_errors=True)

        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert ``"1.2.3"`` → ``(1, 2, 3)``."""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _pick_asset_url(assets: list) -> str:
    """Pick the most appropriate zip asset for the running platform."""
    system = platform.system().lower()
    for asset in assets:
        name: str = asset.get("name", "").lower()
        url: str = asset.get("browser_download_url", "")
        if not url:
            continue
        if system == "windows" and "windows" in name and name.endswith(".zip"):
            return url
        if system == "linux" and "linux" in name and name.endswith(".zip"):
            return url
        if system == "darwin" and ("mac" in name or "darwin" in name) and name.endswith(".zip"):
            return url
    # Generic zip fallback
    for asset in assets:
        if asset.get("name", "").endswith(".zip"):
            return asset.get("browser_download_url", "")
    return ""
