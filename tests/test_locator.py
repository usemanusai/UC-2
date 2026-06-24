import unittest
import os
from unittest.mock import patch
import locator

class TestLocator(unittest.TestCase):

    @patch('os.environ.get')
    def test_get_chrome_user_data_dir_with_localappdata(self, mock_get):
        mock_get.return_value = "C:\\Users\\TestUser\\AppData\\Local"
        expected = os.path.join("C:\\Users\\TestUser\\AppData\\Local", "Google", "Chrome", "User Data")
        self.assertEqual(locator.get_chrome_user_data_dir(), expected)
        mock_get.assert_called_once_with("LOCALAPPDATA", "")

    @patch('os.environ.get')
    def test_get_chrome_user_data_dir_without_localappdata(self, mock_get):
        mock_get.return_value = ""
        expected = os.path.join("", "Google", "Chrome", "User Data")
        self.assertEqual(locator.get_chrome_user_data_dir(), expected)
        mock_get.assert_called_once_with("LOCALAPPDATA", "")

if __name__ == '__main__':
    unittest.main()
