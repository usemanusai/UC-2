import tkinter as tk
from tkinter import ttk
import json

class CaptchaStatsDashboard(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)
        self.title("Local-First Captcha Solver Stats")
        self.geometry("450x300")

        # UI Setup
        frame = ttk.Frame(self, padding="15")
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Overall Statistics", font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 5))

        self.lbl_total = ttk.Label(frame, text="Total Requests: 0")
        self.lbl_total.pack(anchor="w")
        self.lbl_success = ttk.Label(frame, text="Successful Solves: 0")
        self.lbl_success.pack(anchor="w")
        self.lbl_failed = ttk.Label(frame, text="Failed Solves: 0")
        self.lbl_failed.pack(anchor="w")

        ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=10)

        ttk.Label(frame, text="Service Breakdown", font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 5))

        self.tree = ttk.Treeview(frame, columns=("Service", "Requests", "Success", "Failed"), show="headings", height=5)
        self.tree.heading("Service", text="Service")
        self.tree.heading("Requests", text="Requests")
        self.tree.heading("Success", text="Success")
        self.tree.heading("Failed", text="Failed")

        self.tree.column("Service", width=120)
        self.tree.column("Requests", width=80, anchor="center")
        self.tree.column("Success", width=80, anchor="center")
        self.tree.column("Failed", width=80, anchor="center")

        self.tree.pack(fill="both", expand=True)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=10)
        ttk.Button(btn_frame, text="Refresh", command=self.load_stats).pack(side="left")
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right")

        self.load_stats()

    def load_stats(self):
        try:
            from engine.registry.captcha_stats import CaptchaStatsManager
            manager = CaptchaStatsManager()
            data = manager.get_stats()
        except ImportError:
            # Fallback for testing standalone
            try:
                import locator
                filepath = locator.get_absolute_path("engine/registry/configs/captcha_stats.json")
            except ImportError:
                filepath = "engine/registry/configs/captcha_stats.json"

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"total_requests": 0, "successful_solves": 0, "failed_solves": 0, "service_stats": {}}

        self.lbl_total.config(text=f"Total Requests: {data.get('total_requests', 0)}")
        self.lbl_success.config(text=f"Successful Solves: {data.get('successful_solves', 0)}")
        self.lbl_failed.config(text=f"Failed Solves: {data.get('failed_solves', 0)}")

        # Clear tree
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Add data to tree
        for service, stats in data.get("service_stats", {}).items():
            self.tree.insert("", "end", values=(
                service,
                stats.get("requests", 0),
                stats.get("successes", 0),
                stats.get("failures", 0)
            ))
