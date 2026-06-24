import threading
import urllib.request
import logging
from typing import Any

logger = logging.getLogger(__name__)

class ProxySourceWorker(threading.Thread):
    """
    A background thread that periodically fetches fresh proxies from a given HTTP URL
    and loads them into the provided proxy rotator class.
    """
    def __init__(
        self,
        source_url: str,
        update_interval: int,
        proxy_rotator_cls: Any,
        proxy_mode: str = "Rotating Proxies"
    ):
        super().__init__(daemon=True, name="ProxySourceWorkerThread")
        self.source_url = source_url
        self.update_interval = update_interval
        self.proxy_rotator_cls = proxy_rotator_cls
        self.proxy_mode = proxy_mode
        self._stop_event = threading.Event()

    def run(self) -> None:
        logger.info(f"ProxySourceWorker started. Fetching from {self.source_url} every {self.update_interval}s.")
        while not self._stop_event.is_set():
            try:
                self._fetch_and_update()
            except Exception as e:
                logger.error(f"ProxySourceWorker failed to update proxies: {e}")

            # Wait for update_interval, but allow quick shutdown
            self._stop_event.wait(self.update_interval)

    def _fetch_and_update(self) -> None:
        req = urllib.request.Request(self.source_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read().decode('utf-8')

        proxies = [
            line.strip()
            for line in data.splitlines()
            if line.strip() and not line.startswith('#')
        ]

        if proxies:
            self.proxy_rotator_cls.load(proxies, mode=self.proxy_mode)
            logger.info(f"ProxySourceWorker successfully loaded {len(proxies)} proxies from {self.source_url}")
        else:
            logger.warning(f"ProxySourceWorker received empty proxy list from {self.source_url}")

    def stop(self) -> None:
        """Signals the worker thread to stop."""
        self._stop_event.set()
