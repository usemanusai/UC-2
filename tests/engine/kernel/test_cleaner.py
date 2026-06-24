import unittest
from unittest.mock import patch, MagicMock, call
import os
import time

from engine.kernel.cleaner import SessionCleaner

class TestSessionCleaner(unittest.TestCase):

    @patch("engine.kernel.cleaner.platform.system")
    @patch("engine.kernel.cleaner.subprocess.run")
    def test_cleanup_zombie_processes_windows(self, mock_run, mock_system):
        mock_system.return_value = "Windows"

        SessionCleaner.cleanup_zombie_processes()

        mock_run.assert_any_call(
            ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
            capture_output=True, timeout=10, check=False
        )
        mock_run.assert_any_call(
            ["taskkill", "/F", "/IM", "chromedriver.exe", "/T"],
            capture_output=True, timeout=10, check=False
        )
        self.assertEqual(mock_run.call_count, 3)

    @patch("engine.kernel.cleaner.platform.system")
    @patch("engine.kernel.cleaner.subprocess.run")
    def test_cleanup_zombie_processes_linux(self, mock_run, mock_system):
        mock_system.return_value = "Linux"

        SessionCleaner.cleanup_zombie_processes()

        mock_run.assert_any_call(
            ["pkill", "-9", "-f", "chrome"],
            capture_output=True, timeout=10, check=False
        )
        mock_run.assert_any_call(
            ["pkill", "-9", "-f", "chromedriver"],
            capture_output=True, timeout=10, check=False
        )
        self.assertEqual(mock_run.call_count, 2)

    @patch("engine.kernel.cleaner.os.path.exists")
    def test_cleanup_stale_profiles_no_dir(self, mock_exists):
        mock_exists.return_value = False
        # Should return early without exceptions
        SessionCleaner.cleanup_stale_profiles("nonexistent_dir")
        mock_exists.assert_called_once_with("nonexistent_dir")

    @patch("engine.kernel.cleaner.os.path.exists")
    @patch("engine.kernel.cleaner.os.scandir")
    @patch("engine.kernel.cleaner.time.time")
    @patch("engine.kernel.cleaner.os.path.getmtime")
    @patch("engine.kernel.cleaner.shutil.rmtree")
    def test_cleanup_stale_profiles(self, mock_rmtree, mock_getmtime, mock_time, mock_scandir, mock_exists):
        mock_exists.return_value = True
        mock_time.return_value = 10000

        mock_entry_stale = MagicMock()
        mock_entry_stale.is_dir.return_value = True
        mock_entry_stale.path = "stale_dir"

        mock_entry_fresh = MagicMock()
        mock_entry_fresh.is_dir.return_value = True
        mock_entry_fresh.path = "fresh_dir"

        mock_entry_file = MagicMock()
        mock_entry_file.is_dir.return_value = False

        mock_scandir.return_value = [mock_entry_stale, mock_entry_fresh, mock_entry_file]

        # side_effect for getmtime
        def getmtime_side_effect(path):
            if path == "stale_dir":
                return 5000  # Age = 5000 > 3600
            elif path == "fresh_dir":
                return 9000  # Age = 1000 < 3600
            return 0

        mock_getmtime.side_effect = getmtime_side_effect

        SessionCleaner.cleanup_stale_profiles("test_sessions")

        mock_rmtree.assert_called_once_with("stale_dir", ignore_errors=True)

if __name__ == '__main__':
    unittest.main()
