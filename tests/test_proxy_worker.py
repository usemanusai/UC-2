import unittest
from unittest.mock import patch, MagicMock
import threading
import time

from engine.core.proxy_worker import ProxySourceWorker

class DummyProxyRotator:
    loaded_proxies = []
    mode = ""

    @classmethod
    def load(cls, proxies, mode="Rotating Proxies"):
        cls.loaded_proxies = proxies
        cls.mode = mode

class TestProxySourceWorker(unittest.TestCase):
    def setUp(self):
        DummyProxyRotator.loaded_proxies = []
        DummyProxyRotator.mode = ""

    @patch('urllib.request.urlopen')
    def test_proxy_source_worker_fetch_and_update(self, mock_urlopen):
        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.read.return_value = b"http://1.2.3.4:8080\n# comment\n\nsocks5://5.6.7.8:1080\n"
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        worker = ProxySourceWorker(
            source_url="http://fake-proxy-url.com/list.txt",
            update_interval=1,
            proxy_rotator_cls=DummyProxyRotator,
            proxy_mode="Rotating Proxies"
        )

        # Manually call _fetch_and_update for deterministic testing
        worker._fetch_and_update()

        self.assertEqual(DummyProxyRotator.loaded_proxies, ["http://1.2.3.4:8080", "socks5://5.6.7.8:1080"])
        self.assertEqual(DummyProxyRotator.mode, "Rotating Proxies")

    @patch('urllib.request.urlopen')
    def test_proxy_source_worker_thread_lifecycle(self, mock_urlopen):
        # Provide an empty response to prevent errors
        mock_response = MagicMock()
        mock_response.read.return_value = b"http://1.1.1.1:8080"
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        worker = ProxySourceWorker(
            source_url="http://fake-proxy-url.com/list.txt",
            update_interval=1,
            proxy_rotator_cls=DummyProxyRotator,
            proxy_mode="Static Proxies"
        )

        worker.start()
        # Allow it to run its first fetch
        time.sleep(0.1)
        worker.stop()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertTrue(DummyProxyRotator.loaded_proxies)
