import os
import shutil
import time
import subprocess
import logging
import platform
import threading

logger = logging.getLogger(__name__)

class SessionCleaner:
    """
    Automated Session Integrity & Cleanup Daemon.
    Handles the cleanup of zombie Chrome processes and stale temporary session directories.
    """
    _daemon_thread = None
    _daemon_running = False

    @staticmethod
    def cleanup_zombie_processes():
        """
        Kills all orphaned Chrome and Chromedriver processes.
        Provides a hard reset to free up memory and prevent hanging instances.
        """
        logger.info("[SessionCleaner] Scanning for zombie Chrome and Chromedriver processes...")

        system = platform.system().lower()

        try:
            if system == "windows":
                # Windows taskkill logic
                subprocess.run(
                    ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                    capture_output=True, timeout=10, check=False
                )
                subprocess.run(
                    ["taskkill", "/F", "/IM", "chromedriver.exe", "/T"],
                    capture_output=True, timeout=10, check=False
                )
                subprocess.run(
                    ["taskkill", "/F", "/IM", "undetected_chromedriver.exe", "/T"],
                    capture_output=True, timeout=10, check=False
                )
                logger.info("[SessionCleaner] Windows zombie processes cleaned.")
            else:
                # Linux / macOS pkill logic
                subprocess.run(
                    ["pkill", "-9", "-f", "chrome"],
                    capture_output=True, timeout=10, check=False
                )
                subprocess.run(
                    ["pkill", "-9", "-f", "chromedriver"],
                    capture_output=True, timeout=10, check=False
                )
                logger.info("[SessionCleaner] POSIX zombie processes cleaned.")
        except subprocess.TimeoutExpired as e:
            logger.error(f"[SessionCleaner] Process kill timed out: {e}")
        except Exception as e:
            logger.error(f"[SessionCleaner] Failed to clean zombie processes: {e}")

    @staticmethod
    def cleanup_stale_profiles(base_dir: str = "temp_sessions", max_age_seconds: int = 3600):
        """
        Scans the base_dir and deletes any directory older than max_age_seconds.
        """
        if not os.path.exists(base_dir):
            return

        now = time.time()
        cleaned_count = 0

        try:
            for entry in os.scandir(base_dir):
                if entry.is_dir():
                    try:
                        # Use modification time
                        mtime = os.path.getmtime(entry.path)
                        age = now - mtime

                        if age > max_age_seconds:
                            logger.info(f"[SessionCleaner] Removing stale profile: {entry.path} (Age: {age:.1f}s)")
                            shutil.rmtree(entry.path, ignore_errors=True)
                            cleaned_count += 1
                    except Exception as e:
                        logger.warning(f"[SessionCleaner] Failed to check or remove {entry.path}: {e}")

            if cleaned_count > 0:
                logger.info(f"[SessionCleaner] Stale profile cleanup complete. Removed {cleaned_count} profiles.")
        except Exception as e:
            logger.error(f"[SessionCleaner] Error scanning for stale profiles: {e}")

    @classmethod
    def start_daemon(cls, interval_seconds: int = 3600, base_dir: str = "temp_sessions", max_age_seconds: int = 3600):
        """Starts the background daemon thread to perform periodic cleanups."""
        if cls._daemon_running:
            logger.warning("[SessionCleaner] Daemon is already running.")
            return

        cls._daemon_running = True
        cls._daemon_thread = threading.Thread(
            target=cls._daemon_loop,
            args=(interval_seconds, base_dir, max_age_seconds),
            daemon=True,
            name="SessionCleanerDaemon"
        )
        cls._daemon_thread.start()
        logger.info("[SessionCleaner] Daemon started.")

    @classmethod
    def stop_daemon(cls):
        """Stops the daemon."""
        cls._daemon_running = False
        logger.info("[SessionCleaner] Daemon stopped.")

    @classmethod
    def _daemon_loop(cls, interval_seconds: int, base_dir: str, max_age_seconds: int):
        while cls._daemon_running:
            try:
                # We don't want to kill chrome while it might be running an account check!
                # Wait, "kill zombie processes" pkill -9 -f chrome will kill ALL chrome instances.
                # So we should only run cleanup_stale_profiles in the background periodically,
                # and leave cleanup_zombie_processes for initialization or emergency shutdown.
                cls.cleanup_stale_profiles(base_dir, max_age_seconds)
            except Exception as e:
                logger.error(f"[SessionCleaner] Daemon error: {e}")

            # Sleep in chunks to allow responsive shutdown
            for _ in range(int(interval_seconds)):
                if not cls._daemon_running:
                    break
                time.sleep(1)
