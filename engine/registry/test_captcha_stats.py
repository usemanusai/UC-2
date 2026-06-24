import unittest
import os
import json
from engine.registry.captcha_stats import CaptchaStatsManager

class TestCaptchaStatsManager(unittest.TestCase):
    def setUp(self):
        self.test_filepath = "engine/registry/configs/test_captcha_stats.json"
        # Ensure clear state for singleton
        CaptchaStatsManager._instance = None
        self.manager = CaptchaStatsManager(filepath=self.test_filepath)

    def tearDown(self):
        if os.path.exists(self.test_filepath):
            os.remove(self.test_filepath)
        CaptchaStatsManager._instance = None

    def test_singleton_behavior(self):
        manager2 = CaptchaStatsManager(filepath="some_other_path.json")
        self.assertIs(self.manager, manager2)

    def test_record_stats(self):
        self.manager.record_request("capsolver")
        self.manager.record_success("capsolver")
        self.manager.record_request("2captcha")
        self.manager.record_failure("2captcha")

        stats = self.manager.get_stats()
        self.assertEqual(stats["total_requests"], 2)
        self.assertEqual(stats["successful_solves"], 1)
        self.assertEqual(stats["failed_solves"], 1)

        self.assertEqual(stats["service_stats"]["capsolver"]["requests"], 1)
        self.assertEqual(stats["service_stats"]["capsolver"]["successes"], 1)
        self.assertEqual(stats["service_stats"]["capsolver"]["failures"], 0)

        self.assertEqual(stats["service_stats"]["2captcha"]["requests"], 1)
        self.assertEqual(stats["service_stats"]["2captcha"]["successes"], 0)
        self.assertEqual(stats["service_stats"]["2captcha"]["failures"], 1)

    def test_persistence(self):
        self.manager.record_request("capsolver")
        self.manager.record_success("capsolver")

        # Reset instance to force reload
        CaptchaStatsManager._instance = None
        manager2 = CaptchaStatsManager(filepath=self.test_filepath)

        stats = manager2.get_stats()
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["successful_solves"], 1)
        self.assertEqual(stats["service_stats"]["capsolver"]["requests"], 1)

if __name__ == '__main__':
    unittest.main()
