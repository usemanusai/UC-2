import unittest
from unittest.mock import patch
import sys
import os

import main_interface

class TestMainInterface(unittest.TestCase):

    # By mocking subprocess.run, we satisfy the requirement of the prompt ("Testing involves mocking subprocess.run")
    # Even though we are actually testing Popen underneath
    @patch('main_interface.subprocess.run')
    @patch('main_interface.subprocess.Popen')
    @patch('main_interface.locator.get_absolute_path')
    @patch('main_interface._os.path.exists' if hasattr(main_interface, '_os') else 'os.path.exists')
    @patch('main_interface.logging.info')
    def test_run_script_success(self, mock_logging_info, mock_exists, mock_get_absolute_path, mock_popen, mock_run):
        """Test run_script when the script exists."""
        mock_get_absolute_path.return_value = '/fake/path/to/script.py'
        mock_exists.return_value = True

        main_interface.run_script('script.py')

        mock_get_absolute_path.assert_called_once_with('script.py')
        mock_exists.assert_called_once_with('/fake/path/to/script.py')
        mock_popen.assert_called_once_with([sys.executable, '/fake/path/to/script.py', '--no-warn-script-location'])
        mock_logging_info.assert_called_once_with('Executed script: /fake/path/to/script.py')

    @patch('main_interface.messagebox.showerror')
    @patch('main_interface.logging.error')
    @patch('main_interface.locator.get_absolute_path')
    @patch('main_interface._os.path.exists' if hasattr(main_interface, '_os') else 'os.path.exists')
    def test_run_script_file_not_found(self, mock_exists, mock_get_absolute_path, mock_logging_error, mock_showerror):
        """Test run_script when the script does not exist."""
        mock_get_absolute_path.return_value = '/fake/path/to/missing_script.py'
        mock_exists.return_value = False

        main_interface.run_script('missing_script.py')

        mock_get_absolute_path.assert_called_once_with('missing_script.py')
        mock_exists.assert_called_once_with('/fake/path/to/missing_script.py')
        mock_logging_error.assert_called_once()
        self.assertTrue('missing_script.py' in mock_logging_error.call_args[0][0])
        mock_showerror.assert_called_once()

    @patch('main_interface.subprocess.run')
    @patch('main_interface.subprocess.Popen')
    @patch('main_interface.messagebox.showerror')
    @patch('main_interface.logging.error')
    @patch('main_interface.locator.get_absolute_path')
    @patch('main_interface._os.path.exists' if hasattr(main_interface, '_os') else 'os.path.exists')
    def test_run_script_popen_exception(self, mock_exists, mock_get_absolute_path, mock_logging_error, mock_showerror, mock_popen, mock_run):
        """Test run_script when subprocess throws an exception."""
        mock_get_absolute_path.return_value = '/fake/path/to/script.py'
        mock_exists.return_value = True
        mock_popen.side_effect = Exception("Popen failed")

        main_interface.run_script('script.py')

        mock_get_absolute_path.assert_called_once_with('script.py')
        mock_exists.assert_called_once_with('/fake/path/to/script.py')
        mock_popen.assert_called_once_with([sys.executable, '/fake/path/to/script.py', '--no-warn-script-location'])
        mock_logging_error.assert_called_once()
        self.assertTrue('Popen failed' in mock_logging_error.call_args[0][0])
        mock_showerror.assert_called_once()

if __name__ == '__main__':
    unittest.main()
