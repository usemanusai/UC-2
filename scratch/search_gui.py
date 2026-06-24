import re

with open("validator_pro_v2.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

keywords = ["invalid", "captcha", "error", "wrong", "entry_", "var_"]
for idx, line in enumerate(lines):
    if any(k in line.lower() for k in keywords) and ("=" in line or "tk.Entry" in line or "ttk.Entry" in line or "tk.BooleanVar" in line or "tk.StringVar" in line):
        print(f"Line {idx+1}: {line.strip()}")
