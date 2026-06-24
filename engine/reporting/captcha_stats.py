import json
import logging
import os
import threading
from typing import Dict, Any

logger = logging.getLogger(__name__)

class CaptchaStatsManager:
    """
    Singleton manager for tracking and persisting Captcha Solver statistics.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if not cls._instance:
                cls._instance = super(CaptchaStatsManager, cls).__new__(cls)
                cls._instance.storage_path = kwargs.get('storage_path', "engine/registry/captcha_stats.json")
                cls._instance._stats_lock = threading.Lock()
                cls._instance.stats: Dict[str, Dict[str, Any]] = {}
                cls._instance._load_stats()
            return cls._instance

    def _load_stats(self):
        """Loads the stats from the JSON file if it exists."""
        with self._stats_lock:
            try:
                if os.path.exists(self.storage_path):
                    with open(self.storage_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self.stats = data
                else:
                    self.stats = {}
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load captcha stats from {self.storage_path}: {e}")
                self.stats = {}

    def _save_stats(self):
        """Saves the current stats to the JSON file."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=4)
        except IOError as e:
            logger.error(f"Failed to save captcha stats to {self.storage_path}: {e}")

    def record_attempt(self, service: str, success: bool, duration: float):
        """
        Records a single captcha solving attempt.
        """
        with self._stats_lock:
            if service not in self.stats:
                self.stats[service] = {
                    "attempts": 0,
                    "successes": 0,
                    "failures": 0,
                    "total_time": 0.0
                }

            self.stats[service]["attempts"] += 1
            if success:
                self.stats[service]["successes"] += 1
            else:
                self.stats[service]["failures"] += 1

            self.stats[service]["total_time"] += duration

            self._save_stats()

    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns a copy of the current stats.
        """
        with self._stats_lock:
            import copy
            return copy.deepcopy(self.stats)
