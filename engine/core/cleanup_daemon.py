import threading
import time
import logging
import psutil
import os
import shutil

logger = logging.getLogger(__name__)

class CleanupDaemon(threading.Thread):
    """
    Automated Session Integrity & Cleanup Daemon
    Runs in the background to clean up stale temporary session directories
    and kill zombie Chrome processes that might have been left behind.
    """
    def __init__(self, check_interval: int = 300, max_session_age: int = 3600, temp_sessions_dir: str = "temp_sessions"):
        super().__init__(daemon=True, name="CleanupDaemonThread")
        self.check_interval = check_interval
        self.max_session_age = max_session_age
        self.temp_sessions_dir = os.path.abspath(temp_sessions_dir)
        self._stop_event = threading.Event()

    def run(self):
        logger.info(f"CleanupDaemon started. Checking every {self.check_interval}s.")
        while not self._stop_event.is_set():
            try:
                self._cleanup_stale_sessions()
                self._cleanup_zombie_chromes()
            except Exception as e:
                logger.error(f"CleanupDaemon encountered an error: {e}")

            self._stop_event.wait(self.check_interval)

    def _cleanup_stale_sessions(self):
        """Removes session directories older than max_session_age."""
        if not os.path.exists(self.temp_sessions_dir):
            return

        now = time.time()
        for entry in os.scandir(self.temp_sessions_dir):
            try:
                if entry.is_dir() and entry.name.startswith("session_"):
                    age = now - os.path.getmtime(entry.path)
                    if age > self.max_session_age:
                        # Before removing directory, ensure no process holds it
                        try:
                            from engine.kernel.browser_factory import _kill_chrome_processes_for_profile
                            _kill_chrome_processes_for_profile(entry.path)
                        except Exception as ke:
                            logger.warning(f"Failed to kill processes for {entry.path}: {ke}")

                        shutil.rmtree(entry.path, ignore_errors=True)
                        logger.info(f"CleanupDaemon removed stale session: {entry.path}")
            except Exception as e:
                logger.error(f"Error checking session {entry.name}: {e}")

    def _cleanup_zombie_chromes(self):
        """Kills Chrome processes that have no active parent or have been orphaned."""
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'ppid', 'status']):
                try:
                    name = proc.info['name']
                    if name and ('chrome' in name.lower() or 'chromium' in name.lower()):
                        # We only want to touch our own automated chromes, usually identifiable by temp user-data-dir
                        cmdline = proc.info['cmdline']
                        if cmdline and any('temp_sessions' in arg for arg in cmdline):
                            # Zombie check: if it's in zombie status or its parent is PID 1 (orphaned)
                            status = proc.info['status']
                            ppid = proc.info['ppid']
                            if status == psutil.STATUS_ZOMBIE or ppid == 1:
                                proc.kill()
                                logger.info(f"CleanupDaemon killed zombie/orphaned Chrome (PID {proc.info['pid']})")
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        except Exception as e:
            logger.error(f"Error checking zombie processes: {e}")

    def stop(self):
        """Signals the daemon to stop."""
        self._stop_event.set()
