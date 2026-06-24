import pytest
from unittest.mock import patch, MagicMock
import subprocess
import os
import platform

from engine.kernel.browser_factory import _kill_chrome_processes_for_profile

# Need to mock CREATE_NO_WINDOW if not on Windows
if not hasattr(subprocess, "CREATE_NO_WINDOW"):
    subprocess.CREATE_NO_WINDOW = 0x08000000

@patch("engine.kernel.browser_factory.subprocess.run")
@patch("engine.kernel.browser_factory._print_log")
@patch("engine.kernel.browser_factory.platform.system")
def test_kill_chrome_processes_for_profile_windows_exception(mock_system, mock_print_log, mock_subprocess_run):
    mock_system.return_value = "Windows"
    mock_subprocess_run.side_effect = Exception("Mocked Windows Exception")

    # This should not raise an exception, but it should log a warning
    _kill_chrome_processes_for_profile("C:\\TestPath")

    mock_print_log.assert_any_call("Process check/kill failed: Mocked Windows Exception", "WARNING")
    mock_print_log.assert_any_call("Continuing with launch attempt anyway.")

@patch("engine.kernel.browser_factory.subprocess.run")
@patch("engine.kernel.browser_factory.logger")
@patch("engine.kernel.browser_factory.platform.system")
def test_kill_chrome_processes_for_profile_linux_exception(mock_system, mock_logger, mock_subprocess_run):
    mock_system.return_value = "Linux"
    mock_subprocess_run.side_effect = Exception("Mocked Linux Exception")

    # This should not raise an exception, but it should log a warning
    _kill_chrome_processes_for_profile("/home/user/TestPath")

    mock_logger.warning.assert_called_with("[BrowserFactory] Linux profile cleanup failed: Mocked Linux Exception")

@patch("engine.kernel.browser_factory.platform.system")
@patch("engine.kernel.browser_factory._print_log")
@patch("engine.kernel.browser_factory.subprocess.run")
def test_kill_chrome_processes_for_profile_windows_taskkill_exception(mock_subprocess_run, mock_print_log, mock_system):
    mock_system.return_value = "Windows"

    # Setup mock for first subprocess.run (PowerShell) to succeed
    mock_ps_res = MagicMock()
    mock_ps_res.returncode = 0
    # Simulate a process output with matching user data dir
    # Note: the code does abs_path_norm in cmdline_norm
    test_path = os.path.abspath("c:\\testpath")
    # Make sure we use the same path for output
    mock_ps_res.stdout = f"1234||chrome.exe --user-data-dir={test_path}\n"

    # We need to simulate the second subprocess.run (taskkill) failing
    # side_effect can be a list: first item returned, second item raised
    mock_subprocess_run.side_effect = [mock_ps_res, Exception("Mocked Taskkill Exception")]

    # This should not raise an exception, but it should log a warning
    _kill_chrome_processes_for_profile("c:\\testpath")

    # verify the print_log and logger.warning are called as expected
    mock_print_log.assert_any_call("Killing profile-bound zombie Chrome process (PID: 1234)...")
    mock_print_log.assert_any_call("Process check/kill failed: Mocked Taskkill Exception", "WARNING")
    mock_print_log.assert_any_call("Continuing with launch attempt anyway.")
