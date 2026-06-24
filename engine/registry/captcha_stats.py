import json
import logging
import os
import threading
from typing import Dict, Any

logger = logging.getLogger(__name__)

class CaptchaStatsManager:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if not cls._instance:
                cls._instance = super(CaptchaStatsManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, filepath: str = "engine/registry/configs/captcha_stats.json"):
        with self._lock:
            if not self._initialized:
                self.filepath = filepath
                self.stats = {
                    "total_requests": 0,
                    "successful_solves": 0,
                    "failed_solves": 0,
                    "service_stats": {}
                }
                self._load()
                self._initialized = True

    def _load(self):
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self.stats.update(data)
                except Exception as e:
                    logger.error(f"Failed to load captcha stats: {e}")

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(self.stats, f, indent=4)
            except Exception as e:
                logger.error(f"Failed to save captcha stats: {e}")

    def record_request(self, service: str):
        with self._lock:
            self.stats["total_requests"] += 1
            if service not in self.stats["service_stats"]:
                self.stats["service_stats"][service] = {"requests": 0, "successes": 0, "failures": 0}
            self.stats["service_stats"][service]["requests"] += 1
            self._save()

    def record_success(self, service: str):
        with self._lock:
            self.stats["successful_solves"] += 1
            if service not in self.stats["service_stats"]:
                self.stats["service_stats"][service] = {"requests": 0, "successes": 0, "failures": 0}
            self.stats["service_stats"][service]["successes"] += 1
            self._save()

    def record_failure(self, service: str):
        with self._lock:
            self.stats["failed_solves"] += 1
            if service not in self.stats["service_stats"]:
                self.stats["service_stats"][service] = {"requests": 0, "successes": 0, "failures": 0}
            self.stats["service_stats"][service]["failures"] += 1
            self._save()

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.stats)
