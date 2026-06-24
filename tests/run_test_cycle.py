"""
run_test_cycle.py — Non-admin test orchestrator.
Launches tab_monitor + auto_click without requiring admin elevation.
Reads tab_monitor.log and reports result.

Run this AFTER the validator is already running and showing the profile selector.
Or run it at the same time as the validator — it will wait for the validator window.

Usage (from CMD or PowerShell, as Administrator):
    C:/Python313/python.exe run_test_cycle.py
"""
import subprocess, sys, time, os, pathlib, threading, datetime

BASE = pathlib.Path(r"C:\Users\Lenovo ThinkPad T480\Downloads\accounts_checker_builder-main\accounts_checker_builder-main")
PYTHON = r"C:\Python313\python.exe"
LOG = BASE / "tab_monitor.log"

print(f"[TestCycle] {datetime.datetime.now().strftime('%H:%M:%S')} Starting test cycle...")

# ── Step 0: Kill stale chrome ─────────────────────────────────────────────────
print("[TestCycle] Killing stale Chrome processes...")
subprocess.run(["taskkill", "/F", "/IM", "chrome.exe", "/T"], capture_output=True)
subprocess.run(["taskkill", "/F", "/IM", "chromedriver.exe", "/T"], capture_output=True)
time.sleep(1.5)

# ── Step 1: Clear old tab log ─────────────────────────────────────────────────
try:
    LOG.unlink()
    print(f"[TestCycle] Cleared old tab_monitor.log")
except FileNotFoundError:
    pass

# ── Step 2: Start tab monitor ─────────────────────────────────────────────────
print("[TestCycle] Starting tab_monitor...")
monitor_proc = subprocess.Popen(
    [PYTHON, str(BASE / "tab_monitor.py")],
    cwd=str(BASE),
    creationflags=subprocess.CREATE_NO_WINDOW,
)
time.sleep(0.5)

# ── Step 3: Launch validator ──────────────────────────────────────────────────
print("[TestCycle] Launching validator_pro_v2.py...")
validator_proc = subprocess.Popen(
    [PYTHON, str(BASE / "validator_pro_v2.py")],
    cwd=str(BASE),
)

# ── Step 4: Auto-click (after 3s delay) ──────────────────────────────────────
print("[TestCycle] Waiting 15s for validator pre-warming then launching auto_click.py...")
time.sleep(15)
auto_proc = subprocess.Popen(
    [PYTHON, str(BASE / "auto_click.py")],
    cwd=str(BASE),
    creationflags=subprocess.CREATE_NO_WINDOW,
)
auto_proc.wait(timeout=120)
print("[TestCycle] auto_click.py completed.")

# ── Step 5: Monitor tab log for 120s ─────────────────────────────────────────
print("[TestCycle] Watching tab_monitor.log for 120 seconds...")
print("=" * 60)

max_tabs = 0
start = time.time()
last_pos = 0

while time.time() - start < 600:
    try:
        if LOG.exists():
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last_pos)
                chunk = f.read()
                last_pos = f.tell()
            for line in chunk.splitlines():
                if "TABS=" in line:
                    try:
                        n = int(line.split("TABS=")[1].split()[0])
                        if n > max_tabs:
                            max_tabs = n
                        if n > 1:
                            print(f"  *** MULTI-TAB ALERT: {n} TABS: {line.strip()}")
                        elif n == 1:
                            print(f"  [OK] {line.strip()}")
                    except Exception:
                        print(f"  [RAW] {line.strip()}")
                elif line.strip():
                    print(f"  [MON] {line.strip()}")
    except Exception as e:
        pass
    time.sleep(1)

# ── Step 6: Result ────────────────────────────────────────────────────────────
print("=" * 60)
if max_tabs <= 1:
    print(f"[TestCycle] *** PASS *** Max tabs seen: {max_tabs}. Only 1 tab throughout test!")
else:
    print(f"[TestCycle] !!! FAIL !!! Max tabs seen: {max_tabs}. Extra tabs were opened!")

try:
    monitor_proc.terminate()
except Exception:
    pass

print("[TestCycle] Done.")
