import requests
import time

class AntiCaptchaAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.anti-captcha.com"

    def solve(self, task_type, **kwargs):
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": task_type,
                **kwargs
            }
        }
        res = requests.post(f"{self.base_url}/createTask", json=payload).json()
        if res.get("errorId") != 0:
            return None

        task_id = res.get("taskId")
        for _ in range(30):
            time.sleep(2)
            res = requests.post(f"{self.base_url}/getTaskResult", json={"clientKey": self.api_key, "taskId": task_id}).json()
            if res.get("status") == "ready":
                return res.get("solution")
        return None
