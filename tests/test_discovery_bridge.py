import os
import sqlite3
import tempfile
import time
import json
import unittest
from unittest.mock import patch, MagicMock

from engine.core.discovery_bridge import (
    _get_cache_db_path,
    _ensure_cache_table,
    _save_to_cache,
    _load_from_cache,
    _fetch_page_html
)
from engine.core.discovery_schema import DiscoveryResult

class TestDiscoveryBridgeCache(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory to host the test database
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_discovery_results.db")

        # Patch _get_cache_db_path to return our temporary database
        self.patcher = patch('engine.core.discovery_bridge._get_cache_db_path', return_value=self.db_path)
        self.mock_get_path = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_get_cache_db_path(self):
        # We need to temporarily stop the patch to test the original function
        self.patcher.stop()
        try:
            path = _get_cache_db_path()
            self.assertTrue(path.endswith(os.path.join("engine", "registry", "discovery_results.db")))
        finally:
            self.mock_get_path = self.patcher.start()

    def test_ensure_cache_table(self):
        conn = sqlite3.connect(self.db_path)
        try:
            _ensure_cache_table(conn)

            # Verify the table exists and has the right schema
            cursor = conn.execute("PRAGMA table_info(discovery_cache)")
            columns = {row[1]: row[2] for row in cursor.fetchall()}

            self.assertIn("url", columns)
            self.assertIn("result_json", columns)
            self.assertIn("created_at", columns)
            self.assertEqual(columns["url"], "TEXT")
            self.assertEqual(columns["result_json"], "TEXT")
            self.assertEqual(columns["created_at"], "REAL")

            # Should be safe to call again
            _ensure_cache_table(conn)
        finally:
            conn.close()



    @patch('engine.core.discovery_bridge.parse_result')
    def test_save_and_load_cache(self, mock_parse_result):
        # Create a mock DiscoveryResult
        mock_result = MagicMock(spec=DiscoveryResult)

        # When _save_to_cache calls to_gui_dict(), return a dummy dict
        dummy_dict = {"email_field": "#email", "password_field": "#pass"}
        mock_result.to_gui_dict.return_value = dummy_dict

        # When _load_from_cache calls parse_result(), return our mock_result
        mock_parse_result.return_value = mock_result

        target_url = "https://example.com/login"

        # Initially, cache should be empty
        self.assertIsNone(_load_from_cache(target_url))

        # Save to cache
        _save_to_cache(target_url, mock_result)

        # Load from cache
        loaded_result = _load_from_cache(target_url)

        # Verify loaded result is what parse_result returned
        self.assertEqual(loaded_result, mock_result)

        # Verify parse_result was called with the correct dictionary
        mock_parse_result.assert_called_once_with(dummy_dict)

        # Verify data was actually written to the database
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("SELECT result_json FROM discovery_cache WHERE url = ?", (target_url,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(json.loads(row[0]), dummy_dict)
        finally:
            conn.close()


    @patch('engine.core.discovery_bridge.parse_result')
    def test_load_from_cache_expired(self, mock_parse_result):
        target_url = "https://example.com/expired"

        # Manually create the DB and insert an expired record (> 7 days)
        conn = sqlite3.connect(self.db_path)
        try:
            _ensure_cache_table(conn)
            # 8 days ago
            expired_time = time.time() - (8 * 86400)
            dummy_dict = {"email_field": "#old"}
            conn.execute(
                "INSERT INTO discovery_cache (url, result_json, created_at) VALUES (?, ?, ?)",
                (target_url, json.dumps(dummy_dict), expired_time)
            )
            conn.commit()
        finally:
            conn.close()

        # Load from cache should return None for expired records
        loaded_result = _load_from_cache(target_url)
        self.assertIsNone(loaded_result)

        # Verify the record was deleted
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("SELECT * FROM discovery_cache WHERE url = ?", (target_url,))
            self.assertIsNone(cursor.fetchone())
        finally:
            conn.close()

    def test_load_from_cache_no_db(self):
        # Delete the DB file if it exists
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        target_url = "https://example.com/nodb"
        # Should return None smoothly without raising exceptions
        self.assertIsNone(_load_from_cache(target_url))

class TestDiscoveryBridge(unittest.TestCase):
    @patch('requests.get')
    def test_fetch_page_html_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><h1>Hello World</h1></body></html>"
        mock_get.return_value = mock_response

        mock_log_callback = MagicMock()

        result = _fetch_page_html("http://example.com", mock_log_callback)

        self.assertEqual(result, "<html><body><h1>Hello World</h1></body></html>")

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], "http://example.com")
        self.assertIn("headers", kwargs)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertTrue(kwargs["allow_redirects"])

        mock_response.raise_for_status.assert_called_once()

        # Verify log_callback was called at least twice (fetching and fetched)
        self.assertTrue(mock_log_callback.call_count >= 2)
        mock_log_callback.assert_any_call("[Bridge] Fetching page HTML...")
        mock_log_callback.assert_any_call(f"[Bridge] Fetched {len(mock_response.text)} chars of HTML (status 200).")

    @patch('requests.get')
    def test_fetch_page_html_error(self, mock_get):
        mock_get.side_effect = Exception("Mocked connection error")
        mock_log_callback = MagicMock()

        result = _fetch_page_html("http://example.com", mock_log_callback)

        self.assertEqual(result, "")
        mock_log_callback.assert_called_with("[Bridge] HTML fetch failed: Mocked connection error. AI will work from URL context only.")

if __name__ == "__main__":
    unittest.main()
