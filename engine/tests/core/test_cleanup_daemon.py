import os
import time
import shutil
import unittest
from unittest.mock import patch, MagicMock
from engine.core.cleanup_daemon import CleanupDaemon

class TestCleanupDaemon(unittest.TestCase):
    def setUp(self):
        self.temp_dir = "test_temp_sessions"
        os.makedirs(self.temp_dir, exist_ok=True)
        self.daemon = CleanupDaemon(check_interval=1, max_session_age=2, temp_sessions_dir=self.temp_dir)

    def tearDown(self):
        self.daemon.stop()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cleanup_stale_sessions(self):
        stale_dir = os.path.join(self.temp_dir, "session_stale")
        os.makedirs(stale_dir, exist_ok=True)
        old_time = time.time() - 10
        os.utime(stale_dir, (old_time, old_time))

        fresh_dir = os.path.join(self.temp_dir, "session_fresh")
        os.makedirs(fresh_dir, exist_ok=True)

        mock_module = MagicMock()
        mock_kill_func = MagicMock()
        mock_module._kill_chrome_processes_for_profile = mock_kill_func

        with patch.dict('sys.modules', {'engine.kernel.browser_factory': mock_module}):
            self.daemon._cleanup_stale_sessions()

        self.assertFalse(os.path.exists(stale_dir))
        self.assertTrue(os.path.exists(fresh_dir))
        mock_kill_func.assert_called_once_with(os.path.abspath(stale_dir) if not os.path.isabs(stale_dir) else stale_dir)

    @patch("engine.core.cleanup_daemon.psutil")
    def test_cleanup_zombie_chromes(self, mock_psutil):
        mock_proc1 = MagicMock()
        mock_proc1.info = {
            'pid': 100, 'name': 'chrome', 'cmdline': ['--user-data-dir=temp_sessions/session_123'], 'ppid': 1, 'status': 'running'
        }
        mock_proc2 = MagicMock()
        mock_proc2.info = {
            'pid': 101, 'name': 'chromium', 'cmdline': ['--user-data-dir=temp_sessions/session_123'], 'ppid': 200, 'status': 'zombie'
        }
        mock_proc3 = MagicMock()
        mock_proc3.info = {
            'pid': 102, 'name': 'chrome', 'cmdline': ['--user-data-dir=other'], 'ppid': 200, 'status': 'running'
        }

        mock_psutil.process_iter.return_value = [mock_proc1, mock_proc2, mock_proc3]
        mock_psutil.STATUS_ZOMBIE = 'zombie'
        mock_psutil.NoSuchProcess = Exception
        mock_psutil.AccessDenied = Exception
        mock_psutil.ZombieProcess = Exception

        self.daemon._cleanup_zombie_chromes()

        mock_proc1.kill.assert_called_once()
        mock_proc2.kill.assert_called_once()
        mock_proc3.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
