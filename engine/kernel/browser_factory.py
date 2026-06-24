"""
browser_factory.py — Centralized, Self-Healing Browser Initialization Engine

This module is the SINGLE SOURCE OF TRUTH for all undetected-chromedriver
initialization across the entire codebase. Every file that needs to launch
a Chrome browser MUST use this factory.

Self-Healing Features:
1. Automatic Chrome version detection from the actual binary file metadata
2. Automatic purge of stale cached chromedriver executables
3. Self-healing retry loop that parses version mismatch errors and auto-corrects
4. Fresh ChromeOptions on every attempt (prevents reuse errors)
5. Timeout protection — uc.Chrome() can never hang forever
6. Supports both headless (discovery) and visible (checking) modes
"""

import logging
import platform
import subprocess
import threading
import socket
import random
import os
import sys
import shutil
import time
import zipfile
import re
from typing import Optional

import undetected_chromedriver as uc

# ── Monkey-patch for newer `packaging` library compatibility ─────────────────
# packaging >= 22 removed both `.version` and `.vstring` string attributes from
# `Version` objects. undetected_chromedriver's patcher accesses both, causing:
#   AttributeError: 'Version' object has no attribute 'version'
#   AttributeError: 'Version' object has no attribute 'vstring'
# Fix: inject both properties back so they return the string representation.
try:
    from packaging.version import Version as _PkgVersion
    if not hasattr(_PkgVersion, 'version'):
        _PkgVersion.version = property(lambda self: str(self))
    if not hasattr(_PkgVersion, 'vstring'):
        _PkgVersion.vstring = property(lambda self: str(self))
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


def _print_log(msg: str, level: str = "INFO") -> None:
    """
    Bridge: routes browser-factory messages BOTH to the Python logger AND
    directly to stdout so they are visible in the user's terminal alongside
    the print_action() output from validator_pro_v2.py.
    """
    if level == "ERROR":
        logger.error(msg)
    elif level == "WARNING":
        logger.warning(msg)
    else:
        logger.info(msg)
    # Always print directly so the user can see progress in the terminal
    try:
        print(f"[BrowserFactory] {msg}", flush=True)
    except Exception:
        pass


# Module-level flag: chromedriver cache purge only needs to run once per process.
# Purging on every account launch causes race conditions in concurrent mode.
_factory_initialized: bool = False
_factory_init_lock = threading.Lock()

# Cached chromedriver path: resolved once per process so the expensive network
# call to chromedriver_autoinstaller.install() never blocks the account loop.
_cached_chromedriver_path: Optional[str] = None
_chromedriver_cache_lock = threading.Lock()


# =============================================================================
# SECTION 1: Chrome Version Detection (File metadata, NOT folder/registry)
# =============================================================================

def _find_chrome_binary() -> Optional[str]:
    """
    Locates the actual Chrome binary on the system.
    Scans all known installation directories on Windows, Linux, and macOS.
    
    PRIORITY ORDER (2026-06-04 FIX):
      1. REAL Chrome (stable/dev/beta) — the version users actually have.
         Chromedriver is always available for stable Chrome via Selenium Manager.
      2. Playwright Chromium — LAST RESORT only. Its version is
         typically far behind the user's real Chrome, causing
         chromedriver mismatches and session-not-created errors.
    
    Returns the absolute path to the Chrome executable, or None.
    """
    if platform.system() == "Windows":
        # Strategy 1 (PRIMARY): Search for REAL Google Chrome installations
        candidate_dirs = []
        for env_var in ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"]:
            base = os.environ.get(env_var)
            if base:
                candidate_dirs.append(os.path.join(base, "Google", "Chrome", "Application"))
                candidate_dirs.append(os.path.join(base, "Google", "Chrome Dev", "Application"))
                candidate_dirs.append(os.path.join(base, "Google", "Chrome Beta", "Application"))
                candidate_dirs.append(os.path.join(base, "Google", "Chrome SxS", "Application"))

        user_local = os.environ.get("LOCALAPPDATA", "")
        if user_local:
            candidate_dirs.append(os.path.join(user_local, "Google", "Chrome", "Application"))
            candidate_dirs.append(os.path.join(user_local, "Google", "Chrome Dev", "Application"))

        for d in candidate_dirs:
            exe_path = os.path.join(d, "chrome.exe")
            if os.path.isfile(exe_path):
                logger.info(f"[BrowserFactory] Found REAL Chrome binary: {exe_path}")
                return exe_path

        # Strategy 2 (FALLBACK): Playwright Chromium — only if no real Chrome found
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            import glob as _glob
            playwright_pattern = os.path.join(user_profile, "AppData", "Local", "ms-playwright", "chromium-*", "chrome-win64", "chrome.exe")
            matches = _glob.glob(playwright_pattern)
            if matches:
                matches.sort(reverse=True)
                exe_path = matches[0]
                logger.warning(f"[BrowserFactory] No real Chrome found — falling back to Playwright Chromium: {exe_path}")
                return exe_path
    else:
        for name in ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]:
            try:
                result = subprocess.run(["which", name], capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except Exception:
                continue
    return None


def detect_chrome_version() -> Optional[int]:
    """
    Detects the installed Chrome major version from the ACTUAL BINARY FILE.
    
    IMPORTANT: Neither folder names NOR registry values are reliable.
    The ONLY reliable source is the file's own embedded version metadata.
    
    Windows priority:
      1. PowerShell FileVersionInfo (reads the actual PE header of chrome.exe)
      2. Folder structure fallback (less reliable, kept as last resort)
    Linux/Mac:
      1. chrome --version CLI
    
    Returns the major version as int (e.g. 138) or None.
    """
    chrome_path = _find_chrome_binary()

    if platform.system() == "Windows" and chrome_path:
        # Strategy 1: PowerShell FileVersionInfo (THE ground truth)
        # This reads the actual version embedded in the chrome.exe PE header
        try:
            ps_cmd = (
                f'(Get-Item \"{chrome_path}\").VersionInfo.FileVersion'
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                version_str = result.stdout.strip()
                match = re.match(r"(\d+)\.", version_str)
                if match:
                    version = int(match.group(1))
                    logger.info(f"[BrowserFactory] Chrome version from PE header: {version} (full: {version_str})")
                    return version
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
            logger.warning(f"[BrowserFactory] PowerShell version check failed: {e}")

        # Strategy 2: Folder structure fallback (UNRELIABLE — kept as last resort)
        try:
            app_dir = os.path.dirname(chrome_path)
            subdirs = [d for d in os.listdir(app_dir) if os.path.isdir(os.path.join(app_dir, d))]
            for subdir in sorted(subdirs):
                match = re.match(r"(\d+)\.\d+\.\d+\.\d+", subdir)
                if match:
                    version = int(match.group(1))
                    logger.warning(f"[BrowserFactory] Chrome version from folder (UNRELIABLE): {version}")
                    return version
        except Exception as e:
            logger.warning(f"[BrowserFactory] Folder detection failed: {e}")

        logger.error("[BrowserFactory] FAILED to detect Chrome version on Windows.")
        return None

    # Linux/Mac: CLI is reliable
    if chrome_path:
        try:
            result = subprocess.run(
                [chrome_path, "--version"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                match = re.search(r"(\d+)\.", result.stdout)
                if match:
                    version = int(match.group(1))
                    logger.info(f"[BrowserFactory] Chrome version from CLI: {version}")
                    return version
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"[BrowserFactory] Chrome --version failed: {e}")

    logger.error("[BrowserFactory] FAILED to detect Chrome version from any source.")
    return None


def _extract_version_from_error(error_message: str) -> Optional[int]:
    """
    SELF-HEALING: Extracts the actual browser version from a ChromeDriver error.
    Example: 'Current browser version is 138.0.7204.4' → returns 138
    """
    match = re.search(r"Current browser version is (\d+)\.", str(error_message))
    if match:
        version = int(match.group(1))
        logger.info(f"[BrowserFactory] Self-healed: extracted version {version} from error message.")
        return version
    return None


# =============================================================================
# SECTION 2: Stale Chromedriver Cache Purging
# =============================================================================

def purge_stale_chromedriver():
    """
    Deletes ALL cached undetected_chromedriver executables.
    Forces uc to download a fresh driver matching the actual Chrome version.
    """
    cache_dirs = []

    appdata = os.environ.get("APPDATA", "")
    if appdata:
        cache_dirs.append(os.path.join(appdata, "undetected_chromedriver"))

    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        cache_dirs.append(os.path.join(local_appdata, "undetected_chromedriver"))

    home = os.path.expanduser("~")
    if home:
        cache_dirs.append(os.path.join(home, ".local", "share", "undetected_chromedriver"))
        cache_dirs.append(os.path.join(home, "undetected_chromedriver"))

    purged_count = 0
    for cache_dir in cache_dirs:
        if os.path.isdir(cache_dir):
            try:
                # RECURSIVE DELETE of the entire cache folder
                shutil.rmtree(cache_dir, ignore_errors=True)
                purged_count += 1
                logger.info(f"[BrowserFactory] Full purge of cache directory: {cache_dir}")
                # Recreate the folder to avoid 'DirectoryNotFound' if UC expects it
                os.makedirs(cache_dir, exist_ok=True)
            except Exception as e:
                logger.warning(f"[BrowserFactory] Error purging {cache_dir}: {e}")

    if purged_count > 0:
        logger.info(f"[BrowserFactory] Purged {purged_count} stale cache file(s).")
    else:
        logger.info("[BrowserFactory] No stale cache found.")


def _unlock_profile(user_data_dir: str, profile_directory: str):
    """
    SELF-HEALING: Deletes singleton lock files that prevent Chrome from opening
    if a previous session crashed or is still 'bound' to the profile. Also seeds
    exit preferences and purges session storage folders to guarantee a clean,
    single-tab start without crash bubbles or tab restoration.
    """
    if not user_data_dir:
        return

    # Determine profile path
    profile_path = os.path.join(user_data_dir, profile_directory if profile_directory else "Default")
    
    if not os.path.isdir(profile_path):
        # Maybe the profile directory is the user_data_dir itself if not using sub-profiles
        profile_path = user_data_dir

    lock_files = ["SingletonLock", "SingletonCookie", "lock", "parent.lock"]
    
    removed_count = 0
    for lock_name in lock_files:
        lock_path = os.path.join(profile_path, lock_name)
        try:
            if os.path.exists(lock_path):
                if os.path.isfile(lock_path) or os.path.islink(lock_path):
                    os.remove(lock_path)
                    removed_count += 1
                    logger.info(f"[BrowserFactory] Removed profile lock: {lock_path}")
        except Exception as e:
            logger.warning(f"[BrowserFactory] Failed to remove lock {lock_name}: {e}")

    if removed_count > 0:
        logger.info(f"[BrowserFactory] Unlocked profile with {removed_count} file(s) removed.")

    # ── Disable Session Restore and Crash Recovery Bubbles ───────────────────
    prefs_path = os.path.join(profile_path, "Preferences")
    try:
        import json
        if os.path.isfile(prefs_path):
            with open(prefs_path, "r", encoding="utf-8") as pf:
                data = json.load(pf)
        else:
            data = {}
        
        # Ensure profile dictionary structure exists
        if "profile" not in data or not isinstance(data["profile"], dict):
            data["profile"] = {}
        data["profile"]["exit_type"] = "Normal"
        data["profile"]["exited_cleanly"] = True
        
        # Write back updated preferences
        os.makedirs(os.path.dirname(prefs_path), exist_ok=True)
        with open(prefs_path, "w", encoding="utf-8") as pf:
            json.dump(data, pf, indent=2)
        logger.info(f"[BrowserFactory] Standardized crash exit preferences in {prefs_path}")
    except Exception as pe:
        logger.warning(f"[BrowserFactory] Failed to update crash exit preferences in {prefs_path}: {pe}")

    # ── Purge Stale Sessions to Prevent Startup Tab Accumulation ──────────────
    for sub_dir in ["Sessions", "Session Storage"]:
        sd_path = os.path.join(profile_path, sub_dir)
        if os.path.isdir(sd_path):
            try:
                shutil.rmtree(sd_path, ignore_errors=True)
                logger.info(f"[BrowserFactory] Purged stale session directory: {sd_path}")
            except Exception as sde:
                logger.warning(f"[BrowserFactory] Failed to purge session directory {sub_dir}: {sde}")


def _kill_all_chrome_processes():
    """
    HARD RESET: Kills all running chrome.exe and chromedriver.exe processes.
    Ensures no zombie processes are locking the profile or the debugging port.
    """
    logger.info("[BrowserFactory] Performing hard process reset (killing all Chrome/Driver instances)...")
    try:
        if platform.system() == "Windows":
            # Kill chrome.exe
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            # Kill chromedriver.exe (traditional and undetected variants)
            subprocess.run(
                ["taskkill", "/F", "/IM", "chromedriver.exe", "/T"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            subprocess.run(
                ["taskkill", "/F", "/IM", "undetected_chromedriver.exe", "/T"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        else:
            subprocess.run(["pkill", "-9", "-f", "chrome"], capture_output=True, timeout=10)
            subprocess.run(["pkill", "-9", "-f", "chromedriver"], capture_output=True, timeout=10)
    except Exception as e:
        logger.warning(f"[BrowserFactory] Process kill failed or timed out: {e}")


def get_short_path_name(long_name: str) -> str:
    """
    Returns the Windows 8.3 short path version of the input path.
    Has zero external dependencies (uses standard ctypes).
    """
    try:
        if platform.system() != "Windows":
            return long_name
        import ctypes
        needed = ctypes.windll.kernel32.GetShortPathNameW(long_name, None, 0)
        if needed == 0:
            return long_name
        buffer = ctypes.create_unicode_buffer(needed)
        ctypes.windll.kernel32.GetShortPathNameW(long_name, buffer, needed)
        return buffer.value
    except Exception:
        return long_name


def _kill_chrome_processes_for_profile(user_data_dir: str):
    """
    Finds and kills all chrome.exe processes on Windows/Linux that are using
    the specified user_data_dir in their command line arguments.
    This prevents profile lock conflicts and zombie processes without affecting
    concurrent accounts or the user's own browser.
    """
    if not user_data_dir:
        return
    
    # Normalize user_data_dir path to check against command lines
    abs_path = os.path.abspath(user_data_dir)
    # NOTE (2026-06-04 FIX): We NO LONGER skip cleanup for the default User Data
    # directory. The old bypass caused zombie Chrome processes from failed browser
    # factory attempts to hold stale debugging ports, preventing all subsequent
    # launch attempts from succeeding. The kill logic below is SAFE because it
    # only kills processes whose command line contains --remote-debugging-port=
    # (i.e. automation-launched Chrome, NOT the user's manually-opened browser).
    default_user_data = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
    _is_default_profile = abs_path.lower() == os.path.abspath(default_user_data).lower()
    if _is_default_profile:
        _print_log("Default Chrome User Data directory detected — will only kill automation-spawned (--remote-debugging-port) Chrome processes.")
        
    _print_log(f"Cleaning up any existing Chrome processes using profile: {abs_path}")
    
    abs_path_norm = abs_path.lower().replace("/", "\\")
    short_path_norm = get_short_path_name(abs_path).lower().replace("/", "\\")
    
    if platform.system() == "Windows":
        # Formulate a bulletproof command that outputs PID and CommandLine separated by '||'
        ps_cmd = 'Get-CimInstance Win32_Process -Filter "Name = \'chrome.exe\'" | ForEach-Object { "$($_.ProcessId)||$($_.CommandLine)" }'
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    if "||" in line:
                        parts = line.split("||", 1)
                        pid_str = parts[0].strip()
                        cmdline = parts[1].strip()
                        if pid_str.isdigit():
                            cmdline_norm = cmdline.lower().replace("/", "\\")
                            _profile_match = abs_path_norm in cmdline_norm or short_path_norm in cmdline_norm
                            if _profile_match:
                                # For default profile: ONLY kill processes that have
                                # --remote-debugging-port (automation-spawned). Never
                                # kill the user's manually-opened Chrome browser.
                                if _is_default_profile:
                                    if "--remote-debugging-port" not in cmdline_norm:
                                        continue  # Skip — this is the user's own browser
                                _print_log(f"Killing profile-bound zombie Chrome process (PID: {pid_str})...")
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", pid_str],
                                    capture_output=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW
                                )
        except Exception as e:
            _print_log(f"Process check/kill failed: {e}", "WARNING")
            _print_log("Continuing with launch attempt anyway.")
    else:
        # Linux/macOS: match cmdline processes safely
        try:
            res = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines()[1:]:
                    parts = line.strip().split(None, 1)
                    if len(parts) == 2:
                        pid_str, cmdline = parts
                        if pid_str.isdigit():
                            cmdline_norm = cmdline.lower()
                            if abs_path.lower() in cmdline_norm:
                                if _is_default_profile and "--remote-debugging-port" not in cmdline_norm:
                                    continue
                                _print_log(f"Killing profile-bound zombie Chrome process (PID: {pid_str})...")
                                subprocess.run(
                                    ["kill", "-9", pid_str],
                                    capture_output=True
                                )
        except Exception as e:
            logger.warning(f"[BrowserFactory] Linux profile cleanup failed: {e}")


def _prune_tabs_to_one(driver, keep_handle=None, start_url=None) -> Optional[str]:
    """
    Robust tab-pruning helper to ensure exactly one browser tab is opened.
    It switches between window handles, closes all other handles/tabs (e.g. extension
    welcome pages, chrome welcome pages), and switches focus back to the stable handle.
    """
    _print_log("Executing tab pruning helper...")
    try:
        # Give a small stability delay so window handles can register
        time.sleep(0.5)
        handles = driver.window_handles
        if not handles:
            _print_log("No window handles available for pruning.", "WARNING")
            return keep_handle
        
        _print_log(f"Current open handles count: {len(handles)}")
        
        stable_handle = keep_handle
        if not stable_handle or stable_handle not in handles:
            # Look for a handle that successfully responds to executing a basic script
            for h in handles:
                try:
                    driver.switch_to.window(h)
                    driver.execute_script("return 1")
                    # Try to prioritize a handle that is not on chrome:// welcome page if start_url is given
                    try:
                        curr_url = driver.current_url or ""
                        if start_url and start_url.split("?")[0].rstrip("/") in curr_url:
                            stable_handle = h
                            _print_log(f"Found stable handle matching target URL: {curr_url}")
                            break
                    except Exception:
                        pass
                    if not stable_handle:
                        stable_handle = h
                except Exception:
                    pass
            
            # If no handle responded, default to the first handle
            if not stable_handle:
                stable_handle = handles[0]
                _print_log(f"Fallback to first handle as stable handle: {stable_handle}")

        # Close all other handles
        pruned_count = 0
        for h in handles:
            if h != stable_handle:
                try:
                    driver.switch_to.window(h)
                    try:
                        u = driver.current_url or ""
                        _print_log(f"Pruning auxiliary tab: {u}")
                    except Exception:
                        pass
                    driver.close()
                    pruned_count += 1
                except Exception as ce:
                    logger.warning(f"[BrowserFactory] Error closing extra tab: {ce}")
                    
        # Always return focus to our single stable handle
        try:
            driver.switch_to.window(stable_handle)
            _print_log(f"Successfully pruned {pruned_count} auxiliary tabs; focus returned to stable handle: {stable_handle}")
        except Exception as fe:
            logger.warning(f"[BrowserFactory] Error returning focus to stable handle: {fe}")
            
        return stable_handle
    except Exception as e:
        logger.warning(f"[BrowserFactory] Robust tab pruning failed: {e}")
        return keep_handle


# =============================================================================
# SECTION 3: Timeout-Protected uc.Chrome() Launcher
# =============================================================================

def _launch_physical_chrome(chrome_path: str, user_data_dir: str, profile_directory: str, headless: bool, start_url: Optional[str] = None, extra_args: Optional[list] = None) -> tuple:
    """
    Launches the ACTUAL Chrome binary as a standalone process with debugging enabled.
    Finds an open port and waits for it to be ready.
    Returns (port, Popen_object) on success, or (None, None) on failure.
    """
    import socket
    import random

    # 1. Find a random open port
    port = 0
    for _ in range(10):
        p = random.randint(10000, 20000)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', p)) != 0:
                port = p
                break
    if port == 0: port = 9222 # Fallback
    
    _print_log(f"Launching physical Chrome on dynamic port {port} (start_url={start_url})...")
    
    cmd = [chrome_path]
    
    # Mandatory flags
    cmd.extend([
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_directory}",
        "--no-first-run",
        "--no-default-browser-check",
    ])
    
    # Merge extra arguments from options (Incognito, Proxy, UA, etc.)
    if extra_args:
        for arg in extra_args:
            # Avoid duplicating or conflicting with mandatory flags
            if not any(x in arg for x in ["remote-debugging-port", "user-data-dir", "profile-directory"]):
                if arg not in cmd:
                    cmd.append(arg)
    
    if headless:
        cmd.append("--headless=new")
        
    if start_url:
        if start_url.startswith("chrome-extension://"):
            if "about:blank" not in cmd:
                cmd.append("about:blank")
        else:
            if start_url not in cmd:
                cmd.append(start_url)
        
    _print_log(f"Executing Chrome command: {cmd}")
    try:
        if platform.system() == "Windows":
            proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
        else:
            proc = subprocess.Popen(cmd, start_new_session=True)
            
        # 2. POLL FOR PORT LIVENESS (Max 15s)
        start_time = time.time()
        while time.time() - start_time < 15:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    _print_log(f"Chrome debugging port {port} is now ALIVE.")
                    time.sleep(1) # Extra stability buffer
                    return port, proc
            time.sleep(0.5)
            
        _print_log(f"Timed out waiting for Chrome port {port} to open. Terminating process...", "ERROR")
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return None, None
    except Exception as e:
        _print_log(f"Failed to launch physical Chrome: {e}", "ERROR")
        return None, None


def _launch_chrome_with_timeout(fresh_options, headless, use_subprocess, version_to_use, user_data_dir=None, profile_directory=None, chrome_path=None, timeout_seconds=45, start_url=None):
    """
    Launches uc.Chrome() in a separate thread with a hard timeout.
    """
    result_holder = {"driver": None, "error": None}

    def _launch():
        try:
            # 1. ATTACHMENT MODE (Guaranteed stability)
            if chrome_path and user_data_dir:
                # ==============================================================
                # CRITICAL FIX: Extract ALL args from options for the CLI.
                #
                # The original code only read options._arguments.
                # BUT options.add_extension() stores data in options._extensions
                # (a list of base64-encoded CRX blobs) — NEVER in _arguments.
                # This meant ALL extensions added via add_extension() were silently
                # dropped in attachment-mode, causing the "extensions stop working"
                # bug after every Chrome update that triggers the v140+ path.
                #
                # FIX STRATEGY:
                #   1. Extract _arguments as before (picks up --load-extension= too)
                #   2. The new load_chrome_extensions() in validator_pro_v2.py now
                #      ALSO injects --load-extension=<dir> into _arguments, so those
                #      dirs survive here automatically.
                #   3. As belt-and-suspenders: if any _extensions blobs are present
                #      (from legacy add_extension() calls in other code paths), we
                #      write them to the unpacked ext root and add --load-extension=.
                # ==============================================================
                extra_cli_args = []
                if hasattr(fresh_options, '_arguments'):
                    extra_cli_args = list(fresh_options._arguments)
                elif hasattr(fresh_options, 'arguments'):
                    extra_cli_args = list(fresh_options.arguments)

                # Belt-and-suspenders: decode any _extensions blobs and add as
                # --load-extension= if not already covered by _arguments.
                _existing_load_ext = [
                    a for a in extra_cli_args if a.startswith("--load-extension=")
                ]
                _has_extensions = hasattr(fresh_options, '_extensions') and fresh_options._extensions
                _has_extension_files = hasattr(fresh_options, '_extension_files') and fresh_options._extension_files
                if _has_extensions or _has_extension_files:
                    import base64, zipfile as _zf, io as _io, hashlib as _hl, os as _os
                    _unpack_root = _os.path.join(
                        _os.path.dirname(_os.path.abspath(__file__)),
                        "..", "..", "_ext_unpacked"
                    )
                    _unpack_root = _os.path.normpath(_unpack_root)
                    _extra_dirs = []

                    if _has_extensions:
                        for _idx, _ext_blob in enumerate(fresh_options._extensions):
                            try:
                                # _extensions items are base64-encoded CRX bytes.
                                # CRX3 files have a Protobuf binary header before the ZIP
                                # content, so we MUST search for the ZIP PK magic bytes
                                # rather than passing the raw bytes to ZipFile directly.
                                _raw = base64.b64decode(_ext_blob)
                                _h = _hl.md5(_raw).hexdigest()[:8]
                                _udir = _os.path.join(_unpack_root, f"_factory_ext_{_h}")
                                if not _os.path.isfile(_os.path.join(_udir, "manifest.json")):
                                    _os.makedirs(_udir, exist_ok=True)
                                    # Find ZIP magic (handles both CRX2 and CRX3 headers)
                                    _zip_start = _raw.find(b"PK\x03\x04")
                                    if _zip_start == -1:
                                        _print_log(f"Extension blob {_idx}: no ZIP magic found — skipping.", "WARNING")
                                        continue
                                    _zip_bytes = _raw[_zip_start:]
                                    with _zf.ZipFile(_io.BytesIO(_zip_bytes)) as _zfile:
                                        _zfile.extractall(_udir)
                                if _os.path.isfile(_os.path.join(_udir, "manifest.json")):
                                    _extra_dirs.append(_udir)
                                    _print_log(f"Belt-and-suspenders: unpacked extension blob {_idx} → {_udir}")
                            except Exception as _ee:
                                _print_log(f"Failed to unpack extension blob {_idx}: {_ee}", "WARNING")

                    if _has_extension_files:
                        for _idx, _ext_file in enumerate(fresh_options._extension_files):
                            try:
                                if _os.path.isdir(_ext_file):
                                    if _os.path.isfile(_os.path.join(_ext_file, "manifest.json")):
                                        _extra_dirs.append(_os.path.abspath(_ext_file))
                                        _print_log(f"Belt-and-suspenders: added extension directory {_idx} → {_ext_file}")
                                elif _os.path.isfile(_ext_file) and str(_ext_file).lower().endswith(".crx"):
                                    with open(_ext_file, "rb") as f:
                                        _raw = f.read()
                                    _h = _hl.md5(_raw).hexdigest()[:8]
                                    _udir = _os.path.join(_unpack_root, f"_factory_ext_file_{_h}")
                                    if not _os.path.isfile(_os.path.join(_udir, "manifest.json")):
                                        _os.makedirs(_udir, exist_ok=True)
                                        _zip_start = _raw.find(b"PK\x03\x04")
                                        if _zip_start == -1:
                                            _print_log(f"Extension file {_ext_file}: no ZIP magic found — skipping.", "WARNING")
                                            continue
                                        _zip_bytes = _raw[_zip_start:]
                                        with _zf.ZipFile(_io.BytesIO(_zip_bytes)) as _zfile:
                                            _zfile.extractall(_udir)
                                    if _os.path.isfile(_os.path.join(_udir, "manifest.json")):
                                        _extra_dirs.append(_udir)
                                        _print_log(f"Belt-and-suspenders: unpacked extension file {_ext_file} → {_udir}")
                            except Exception as _ee:
                                _print_log(f"Failed to process extension file {_ext_file}: {_ee}", "WARNING")

                    if _extra_dirs:
                        # Merge with existing --load-extension args, deduplicate
                        _existing_dirs = []
                        for _a in _existing_load_ext:
                            _existing_dirs.extend(_a[len("--load-extension="):].split(","))
                        _all_dirs = list(dict.fromkeys(_existing_dirs + _extra_dirs))
                        # Remove stale --load-extension args
                        extra_cli_args = [a for a in extra_cli_args if not a.startswith("--load-extension=")]
                        extra_cli_args.append(f"--load-extension={','.join(_all_dirs)}")
                        logger.info(f"[BrowserFactory] Injected --load-extension for {len(_all_dirs)} total extension dir(s).")

                port, chrome_proc = _launch_physical_chrome(chrome_path, user_data_dir, profile_directory, headless, start_url, extra_cli_args)
                if port:
                    _print_log(f"Attaching to physical Chrome on port {port}...")
                    
                    from selenium import webdriver
                    from selenium.webdriver.chrome.options import Options as SeleniumOptions
                    from selenium.webdriver.chrome.service import Service
                    
                    # ==============================================================
                    # TIMEOUT-PROTECTED CHROMEDRIVER RESOLUTION
                    # chromedriver_autoinstaller.install() makes network requests
                    # (to googlechromelabs.github.io) with NO built-in timeout.
                    # Wrapping in a thread with a hard 30-second timeout prevents
                    # the entire account loop from freezing on a slow connection.
                    # The resolved path is cached globally so this only ever
                    # makes one network call per process lifetime.
                    # ==============================================================
                    global _cached_chromedriver_path
                    driver_exe = None
                    with _chromedriver_cache_lock:
                        # 2026-06-04 FIX: We no longer have a special "Playwright Chromium"
                        # path that picks a hardcoded older version of chromedriver from the workspace root.
                        # Instead we ALWAYS let Selenium Manager resolve the correct
                        # chromedriver matching the ACTUAL Chrome binary we launched.
                        # This prevents the #1 failure mode: an older chromedriver version trying
                        # to control a newer Chrome version → session-not-created.
                        if _cached_chromedriver_path and os.path.isfile(_cached_chromedriver_path):
                            driver_exe = _cached_chromedriver_path
                            _print_log(f"Using cached chromedriver: {driver_exe}")
                        else:
                            _local_driver = os.path.join(os.getcwd(), "chromedriver.exe")
                            if not os.path.isfile(_local_driver):
                                _sib_driver = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "chromedriver.exe"))
                                if os.path.isfile(_sib_driver):
                                    _local_driver = _sib_driver
                            
                            _is_valid_win = False
                            _local_cd_version_ok = False
                            if os.path.isfile(_local_driver):
                                try:
                                    with open(_local_driver, "rb") as _df:
                                        _magic = _df.read(2)
                                        if _magic == b"MZ":
                                            # Try executing to ensure it is a valid Win32 application
                                            # NOTE: subprocess already imported at module level — no local import needed
                                            creationflags = 0
                                            if platform.system() == "Windows":
                                                creationflags = subprocess.CREATE_NO_WINDOW
                                            # Run with a short timeout and check for WinError 193/OSError
                                            res = subprocess.run(
                                                [_local_driver, "--version"],
                                                capture_output=True,
                                                text=True,
                                                timeout=5,
                                                creationflags=creationflags
                                            )
                                            _is_valid_win = True
                                            # 2026-06-04 FIX: Also verify major version matches Chrome.
                                            # A workspace older chromedriver version cannot control a newer Chrome version.
                                            if res.returncode == 0 and res.stdout.strip():
                                                _cd_ver_match = re.search(r'(\d+)\.', res.stdout.strip())
                                                if _cd_ver_match:
                                                    _cd_major = int(_cd_ver_match.group(1))
                                                    _chrome_major = detect_chrome_version()
                                                    if _chrome_major and _cd_major == _chrome_major:
                                                        _local_cd_version_ok = True
                                                        _print_log(f"Local chromedriver v{_cd_major} matches Chrome v{_chrome_major}.")
                                                    else:
                                                        if _chrome_major is None:
                                                            _print_log(f"Local chromedriver v{_cd_major} does NOT match (Chrome version undetected). Skipping.", "WARNING")
                                                        else:
                                                            _print_log(f"Local chromedriver v{_cd_major} does NOT match Chrome v{_chrome_major}. Skipping.", "WARNING")
                                except Exception as test_e:
                                    _print_log(f"Verification of local chromedriver failed: {test_e}. Bypassing.", "WARNING")

                            if _is_valid_win and _local_cd_version_ok:
                                driver_exe = _local_driver
                                _cached_chromedriver_path = driver_exe
                                _print_log(f"Found local Windows chromedriver in workspace: {driver_exe}")
                            else:
                                if os.path.isfile(_local_driver):
                                    if _is_valid_win and not _local_cd_version_ok:
                                        _print_log("Local chromedriver in workspace has WRONG VERSION for current Chrome. Bypassing.", "WARNING")
                                    elif not _is_valid_win:
                                        _print_log("Local chromedriver in workspace is NOT a valid Windows executable. Bypassing.", "WARNING")
                                _print_log("Resolving chromedriver path (one-time network call, 15s timeout)...")
                                try:
                                    # Set socket timeout to prevent autoinstaller from hanging indefinitely
                                    import socket
                                    socket.setdefaulttimeout(15)
                                except Exception:
                                    pass

                                resolved_path = None
                                resolved_error = None
                                
                                # Check user cache directory first for already resolved driver matching the version
                                try:
                                    major_ver = detect_chrome_version()
                                    user_profile = os.environ.get("USERPROFILE")
                                    if user_profile and major_ver:
                                        import glob as _glob
                                        cache_pattern = os.path.join(
                                            user_profile, 
                                            ".cache", 
                                            "selenium", 
                                            "chromedriver", 
                                            "win64", 
                                            f"{major_ver}.*", 
                                            "chromedriver.exe"
                                        )
                                        matches = _glob.glob(cache_pattern)
                                        if matches:
                                            matches.sort(reverse=True)
                                            resolved_path = matches[0]
                                            _print_log(f"Found cached driver in user directory matching Chrome v{major_ver}: {resolved_path}")
                                except Exception as ce:
                                    _print_log(f"User cache lookup failed: {ce}", "WARNING")
                                
                                # Try Selenium Manager first (synchronously) if not already found in cache
                                if not resolved_path:
                                    try:
                                        # NOTE: subprocess, sys, json all imported at module level
                                        import json
                                        selenium_sm = None
                                        for path in sys.path:
                                            if "site-packages" in path:
                                                sm_path = os.path.join(path, "selenium", "webdriver", "common", "windows", "selenium-manager.exe")
                                                if os.path.isfile(sm_path):
                                                    selenium_sm = sm_path
                                                    break
                                        if not selenium_sm and platform.system() != "Windows":
                                            for path in sys.path:
                                                if "site-packages" in path:
                                                    sm_path = os.path.join(path, "selenium", "webdriver", "common", "linux", "selenium-manager")
                                                    if os.path.isfile(sm_path):
                                                        selenium_sm = sm_path
                                                        break
                                        
                                        if selenium_sm and chrome_path:
                                            _print_log(f"Resolving driver via Selenium Manager for binary: {chrome_path}")
                                            cmd = [selenium_sm, "--browser", "chrome", "--browser-path", chrome_path, "--output", "json"]
                                            
                                            creationflags = 0
                                            if platform.system() == "Windows":
                                                creationflags = subprocess.CREATE_NO_WINDOW
                                                
                                            res = subprocess.run(
                                                cmd, 
                                                capture_output=True, 
                                                text=True, 
                                                timeout=15,
                                                stdin=subprocess.DEVNULL,
                                                creationflags=creationflags
                                            )
                                            if res.returncode == 0:
                                                data = json.loads(res.stdout)
                                                resolved = data.get("result", {}).get("driver_path")
                                                if resolved and os.path.isfile(resolved):
                                                    resolved_path = resolved
                                                    _print_log(f"Selenium Manager successfully resolved driver: {resolved}")
                                    except Exception as sm_e:
                                        _print_log(f"Selenium Manager resolution failed/timed out: {sm_e}. Falling back to autoinstaller.", "WARNING")

                                # Fallback to autoinstaller
                                if not resolved_path:
                                    try:
                                        import chromedriver_autoinstaller
                                        resolved_path = chromedriver_autoinstaller.install()
                                    except Exception as _re:
                                        resolved_error = str(_re)

                                if resolved_error:
                                    _print_log(f"chromedriver_autoinstaller error: {resolved_error}. Falling back to PATH.", "WARNING")
                                elif resolved_path and os.path.isfile(resolved_path):
                                    driver_exe = resolved_path
                                    _cached_chromedriver_path = driver_exe
                                    _print_log(f"Chromedriver resolved and cached: {driver_exe}")

                            # Final fallback: search system PATH for chromedriver.exe
                            if not driver_exe:
                                import shutil as _shutil
                                _found = _shutil.which("chromedriver") or _shutil.which("chromedriver.exe")
                                if _found:
                                    driver_exe = _found
                                    _cached_chromedriver_path = driver_exe
                                    _print_log(f"Chromedriver found in PATH: {driver_exe}")
                                else:
                                    _print_log("Could not find chromedriver by any method. Selenium will attempt auto-discovery.", "WARNING")

                    attach_options = SeleniumOptions()
                    attach_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
                    
                    # Stability Args
                    attach_options.add_argument("--disable-gpu")
                    attach_options.add_argument("--no-sandbox")
                    
                    service = Service(executable_path=driver_exe) if driver_exe else None
                    
                    # We use standard Selenium for the bridge as it's the most reliable for port attachment
                    try:
                        if service:
                            driver = webdriver.Chrome(service=service, options=attach_options)
                        else:
                            driver = webdriver.Chrome(options=attach_options)
                    except Exception as first_e:
                        logger.warning(f"[BrowserFactory] Failed to attach using resolved chromedriver ({driver_exe}): {first_e}. Retrying with Selenium Manager auto-discovery...")
                        driver_exe = None
                        _cached_chromedriver_path = None
                        try:
                            driver = webdriver.Chrome(options=attach_options)
                        except Exception as second_e:
                            logger.error(f"[BrowserFactory] Auto-discovery fallback failed: {second_e}")
                            raise second_e

                    # Hook driver.quit to terminate the physical Chrome process tree
                    if chrome_proc:
                        original_quit = driver.quit
                        # IMPORTANT: capture subprocess as default arg to avoid
                        # "cannot access free variable 'subprocess'" in closure
                        def custom_quit(_subprocess=subprocess, _platform=platform,
                                        _os=os, _chrome_proc=chrome_proc,
                                        _user_data_dir=user_data_dir):
                            _print_log("Custom quit called: killing Chrome process tree first and closing Selenium session...")
                            # 1. Kill the process tree immediately to prevent hangs in Selenium quit/close
                            try:
                                pid = _chrome_proc.pid
                                if _platform.system() == "Windows":
                                    _subprocess.run(
                                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                                        capture_output=True,
                                        creationflags=_subprocess.CREATE_NO_WINDOW
                                    )
                                else:
                                    import signal
                                    try:
                                        _os.killpg(_os.getpgid(pid), signal.SIGKILL)
                                    except Exception:
                                        try:
                                            _chrome_proc.kill()
                                        except Exception:
                                            pass
                            except Exception as ke:
                                _print_log(f"Error terminating Chrome process tree: {ke}", "WARNING")

                            # 2. Force-kill any lingering background processes bound to this profile directory
                            if _user_data_dir:
                                _kill_chrome_processes_for_profile(_user_data_dir)

                            # 3. Clean up the Selenium webdriver session
                            try:
                                driver.close()
                            except Exception:
                                pass
                            try:
                                original_quit()
                            except Exception as eq:
                                _print_log(f"Original quit failed: {eq}", "WARNING")
                        driver.quit = custom_quit
                    
                    # ─── Window Handle Recovery ────────────────────────────────
                    # Fresh isolated-session profiles cause Chrome to open extra
                    # tabs (welcome page, crash-restore dialogs, etc.) before the
                    # initial URL tab becomes the active handle.  We must switch
                    # to a live handle BEFORE calling execute_script(), otherwise
                    # "target window already closed" propagates and kills the attempt.
                    _stable_handle = None
                    for _attempt_wh in range(8):
                        try:
                            _handles = driver.window_handles
                            if _handles:
                                # Switch to the first open handle and verify it responds
                                driver.switch_to.window(_handles[0])
                                driver.execute_script("return 1")
                                _stable_handle = _handles[0]
                                break
                        except Exception as _wh_e:
                            logger.warning(f"[BrowserFactory] Window handle attempt {_attempt_wh+1}: {_wh_e}")
                            time.sleep(1)

                    if not _stable_handle:
                        raise Exception("No stable window handle found after Chrome launch.")

                    # Close all other window handles to prevent tab accumulation
                    _stable_handle = _prune_tabs_to_one(driver, keep_handle=_stable_handle, start_url=start_url)

                    # Verify Liveness
                    logger.info(f"[BrowserFactory] Verifying driver liveness...")
                    driver.execute_script("return 1")
                    
                    # Set a timeout for navigation specifically
                    driver.set_page_load_timeout(30)
                    
                    # DOUBLE-VERIFIED NAVIGATION: If we have a start_url, force it again after attachment
                    if start_url:
                        try:
                            current_url = driver.current_url or ""
                            comp_target = start_url.split("?")[0].rstrip("/")
                            comp_current = current_url.split("?")[0].rstrip("/")
                            if comp_current.startswith(comp_target) and "about:blank" not in current_url:
                                logger.info(f"[BrowserFactory] Already on target URL: {current_url}. Skipping redundant navigation.")
                            else:
                                logger.info(f"[BrowserFactory] Performing secondary navigation to: {start_url}")
                                try:
                                    # Level 1: Standard Navigate
                                    driver.get(start_url)
                                except Exception as e:
                                    logger.warning(f"[BrowserFactory] Level 1 Navigate failed: {e}. Trying Level 2 (JS)...")
                                    # Level 2: JavaScript Force-Jump
                                    try:
                                        driver.execute_script(f"window.location.href = '{start_url}';")
                                    except Exception as e2:
                                        logger.error(f"[BrowserFactory] Level 2 Force-Jump failed: {e2}. Trying Level 3 (Visual)...")
                        except Exception as ne:
                            logger.warning(f"[BrowserFactory] Navigation check failed: {ne}. Forcing navigate...")
                            try:
                                driver.get(start_url)
                            except Exception:
                                pass
                        
                        # Level 3: VISUAL FALLBACK (PyAutoGUI)
                        # If after 5 seconds we aren't where we want to be, type it in manually
                        time.sleep(5)
                        try:
                            current_url = driver.current_url
                            if "chrome://" in current_url or "duckduckgo" in current_url or "about:blank" in current_url:
                                logger.info(f"[BrowserFactory] Browser stuck on {current_url}. Activating Visual Injection!")
                                # Bring window to front
                                driver.maximize_window()
                                time.sleep(1)
                                try:
                                    import pyautogui
                                    # Control+L (Focus address bar)
                                    pyautogui.hotkey('ctrl', 'l')
                                    time.sleep(0.5)
                                    # Type URL and press Enter
                                    pyautogui.typewrite(start_url)
                                    pyautogui.press('enter')
                                    logger.info(f"[BrowserFactory] Visual injection completed.")
                                    time.sleep(3)
                                except Exception as pye:
                                    logger.warning(f"[BrowserFactory] pyautogui visual injection skipped: {pye}")
                        except Exception as ve:
                             logger.warning(f"[BrowserFactory] Visual injection skipped: {ve}")

                    # ── SECOND-PASS TAB PRUNING ────────────────────────────────
                    # Extension welcome pages load ASYNCHRONOUSLY during Chrome
                    # startup, often after the first pruning has already run.
                    # This final pass catches and closes any tabs that appeared
                    # during the navigation/visual-injection wait block above.
                    _stable_handle = _prune_tabs_to_one(driver, keep_handle=_stable_handle, start_url=start_url)
                    _print_log("Second-pass tab pruning complete; driver is ready.")

                    # ── TAG THE CDP DEBUG PORT ON THE DRIVER ──────────────────
                    # MUST happen BEFORE the extension configurator thread starts,
                    # because the thread reads driver._cdp_debug_port immediately.
                    try:
                        driver._cdp_debug_port = port
                        _print_log(f"CDP debug port {port} stamped onto driver object.")
                    except Exception:
                        pass

                    # ── CONFIGURE EXTENSION SETTINGS VIA CDP ─────────────────
                    # After a clean attach, configure extension-specific storage
                    # settings (rektCaptcha Auto-Open + Auto-Solve = ON).
                    # Runs in a background daemon thread so the main validator
                    # flow is never delayed.
                    try:
                        import sys as _sys
                        # project root is two levels above engine/kernel/
                        _proj_root = os.path.normpath(
                            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
                        )
                        if _proj_root not in _sys.path:
                            _sys.path.insert(0, _proj_root)
                        from extension_configurator import configure_extensions_post_launch as _cfg_ext
                        # Collect the unpacked extension directories from --load-extension= arg
                        _ext_dir_list = []
                        for _arg in (fresh_options.arguments or []):
                            if _arg.startswith("--load-extension="):
                                _ext_dir_list = _arg.replace("--load-extension=", "").split(",")
                                break
                        import threading as _t
                        _cfg_thread = _t.Thread(
                            target=_cfg_ext,
                            args=(driver, _ext_dir_list),
                            kwargs={"timeout": 20.0},
                            daemon=True,
                        )
                        _cfg_thread.start()
                        _print_log(
                            f"Extension configurator launched on port {port} "
                            f"(ext_dirs={len(_ext_dir_list)}, timeout=20s) "
                            f"[rektCaptcha Auto-Open/Auto-Solve]"
                        )
                    except Exception as _cfg_e:
                        _print_log(f"Extension configurator skipped: {_cfg_e}", "WARNING")

                    result_holder["driver"] = driver
                    return



            # 2. FALLBACK: Normal UC launch (if attachment fails)
            launch_args = {
                "options": fresh_options,
                "user_data_dir": user_data_dir,
                "profile_directory": profile_directory,
                "browser_executable_path": chrome_path,
                "headless": headless,
                "use_subprocess": use_subprocess,
                "patcher_force_close": True,
                "suppress_welcome": True,
            }
            if version_to_use:
                launch_args["version_main"] = version_to_use

            try:
                result_holder["driver"] = uc.Chrome(**launch_args)
            except AttributeError as _ver_attr_e:
                # 'Version' object has no attribute 'version' — newer packaging lib
                # incompatibility with undetected_chromedriver. Retry without version_main.
                _print_log(f"version_main={version_to_use} caused AttributeError ({_ver_attr_e}); retrying without version_main.", "WARNING")
                launch_args.pop("version_main", None)
                result_holder["driver"] = uc.Chrome(**launch_args)

            
        except Exception as e:
            result_holder["error"] = str(e)
            _print_log(f"Launch thread error: {e}", "ERROR")

    thread = threading.Thread(target=_launch, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        _print_log(f"Chrome launch TIMED OUT after {timeout_seconds}s — killing hung processes.", "ERROR")
        _kill_all_chrome_processes()
        return None, f"uc.Chrome() timed out after {timeout_seconds} seconds"

    if result_holder["error"]:
        return None, result_holder["error"]

    return result_holder["driver"], None


# =============================================================================
# SECTION 4: Self-Healing Chrome Launcher (THE core function)
# =============================================================================

def create_chrome(
    options: Optional[uc.ChromeOptions] = None,
    headless: bool = False,
    use_subprocess: bool = True,
    user_data_dir: Optional[str] = None,
    profile_directory: Optional[str] = None,
    max_retries: int = 5,
    extra_arguments: Optional[list] = None,
    start_url: Optional[str] = None,
    kill_existing: bool = False,
) -> Optional[uc.Chrome]:
    """
    THE SINGLE ENTRY POINT for creating a Chrome browser instance.
    Self-healing, future-proof, timeout-protected, and fully automated.

    Parameters
    ----------
    kill_existing : bool
        When True, terminates ALL running Chrome / ChromeDriver processes before
        launching.  Default is False — for concurrent per-account isolation you
        MUST leave this False, otherwise each new account would kill all the
        Chrome windows of previously-started accounts.
    """
    global _factory_initialized

    # Step 1: Detect Chrome version from the actual binary PE header
    detected_version = detect_chrome_version()
    if detected_version:
        _print_log(f"Detected Chrome version: {detected_version}")
    else:
        _print_log("Could not detect Chrome version. Will self-heal from errors.", "WARNING")

    # Step 2: ONE-TIME initialisation — purge stale chromedriver cache on the
    # very first call in this process only.  Purging on every account launch
    # creates race conditions when multiple sessions start simultaneously.
    with _factory_init_lock:
        if not _factory_initialized:
            purge_stale_chromedriver()
            _factory_initialized = True
            _print_log("One-time cache purge complete.")

    # Step 3: Optionally kill existing Chrome processes.
    # Only do this when the caller explicitly requests it (e.g. a single-account
    # run or a hard-reset scenario).  Never call this in concurrent mode.
    if kill_existing:
        logger.info("[BrowserFactory] kill_existing=True — terminating all Chrome/Driver processes.")
        _kill_all_chrome_processes()

    # Unlock profile if specified (kill any active profile-bound zombie processes first)
    if user_data_dir:
        _kill_chrome_processes_for_profile(user_data_dir)
        _unlock_profile(user_data_dir, profile_directory)

    # Collect original option arguments so we can recreate fresh options each attempt
    original_arguments = []
    if options:
        if hasattr(options, '_arguments'):
            original_arguments = list(options._arguments)
        elif hasattr(options, 'arguments'):
            original_arguments = list(options.arguments)

    if extra_arguments:
        original_arguments.extend(extra_arguments)

    # Track the version to use across attempts (may change via self-healing)
    version_to_use = detected_version

    for attempt in range(1, max_retries + 1):
        # Step 3: Create FRESH ChromeOptions for every attempt
        fresh_options = uc.ChromeOptions()
        if options:
            if hasattr(options, '_extensions'):
                fresh_options._extensions = list(options._extensions)
            if hasattr(options, '_extension_files'):
                fresh_options._extension_files = list(options._extension_files)

        for arg in original_arguments:
            fresh_options.add_argument(arg)

        if user_data_dir:
            temp_args = [a for a in original_arguments if "user-data-dir" not in a]
        else:
            temp_args = list(original_arguments)

        if profile_directory:
            temp_args = [a for a in temp_args if "profile-directory" not in a]

        if headless:
            has_headless = any("headless" in a for a in temp_args)
            if not has_headless:
                fresh_options.add_argument("--headless")
                fresh_options.add_argument("--disable-gpu")
                fresh_options.add_argument("--no-sandbox")
        
        # Stability Enhancements (Production-Ready)
        fresh_options.add_argument("--disable-dev-shm-usage")
        fresh_options.add_argument("--no-first-run")
        fresh_options.add_argument("--no-default-browser-check")
        # NOTE: --disable-popup-blocking was REMOVED. It caused window.open() calls on
        # the target website and extensions to open unrestricted new tabs, resulting in
        # multiple Chrome tab windows visible to the user. Chrome's native popup blocker
        # is now re-enabled. Any legitimate popups needed for automation (captcha, etc.)
        # are handled by the CDP sweeper and explicit window handle management in check_account().
        fresh_options.add_argument("--ignore-certificate-errors")
        fresh_options.add_argument("--disable-blink-features=AutomationControlled")


        version_label = version_to_use if version_to_use else "auto-detect"
        _print_log(f"Init attempt {attempt}/{max_retries} (version_main={version_label})...")

        # Step 4: Launch with hard timeout protection (45 seconds)
        chrome_path = _find_chrome_binary()
        
        current_version = version_to_use
        if attempt == 1 and version_to_use and version_to_use >= 140:
             current_version = None
             _print_log("Attempt 1: Skipping explicit version_main to avoid hangs on v140+.")

        driver, error_msg = _launch_chrome_with_timeout(
            fresh_options=fresh_options,
            headless=headless,
            use_subprocess=False,
            version_to_use=current_version,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
            chrome_path=chrome_path,
            timeout_seconds=30,
            start_url=start_url,
        )

        if driver:
            _print_log(f"Browser launched successfully on attempt {attempt}.")
            # Final safety prune: the uc.Chrome() fallback path does not go through
            # _launch_chrome_with_timeout, so asynchronous extension welcome pages
            # could still be open. This call is a no-op if only one tab is open.
            try:
                _prune_tabs_to_one(driver, start_url=start_url)
            except Exception as _final_prune_e:
                logger.warning(f"[BrowserFactory] Final safety prune failed: {_final_prune_e}")
            # ── STAMP CDP DEBUG PORT (covers all launch paths) ────────────────
            # The attachment-mode branch already stamps this inside
            # _launch_chrome_with_timeout, but the UC-fallback path (uc.Chrome)
            # doesn't go through that code path. We stamp here as a universal
            # guarantee so validator_pro_v2._get_cdp_debug_port() never returns None.
            if not getattr(driver, "_cdp_debug_port", None):
                try:
                    debug_addr = driver.capabilities.get(
                        "goog:chromeOptions", {}
                    ).get("debuggerAddress", "")
                    if debug_addr and ":" in debug_addr:
                        driver._cdp_debug_port = int(debug_addr.split(":")[-1])
                        _print_log(
                            f"CDP debug port {driver._cdp_debug_port} stamped from capabilities "
                            f"(UC fallback path)."
                        )
                except Exception as _stamp_e:
                    _print_log(f"CDP port stamp from capabilities failed: {_stamp_e}", "WARNING")
            # Hook the quit method on the returned driver to guarantee profile process cleanup on exit,
            # covering both primary attachment launches and fallback undetected-chromedriver sessions.
            if user_data_dir:
                original_quit = driver.quit
                def wrapped_quit():
                    _print_log("Wrapped quit called: cleaning up profile processes...")
                    try:
                        original_quit()
                    except Exception as eq:
                        _print_log(f"Original quit failed: {eq}", "WARNING")
                    _kill_chrome_processes_for_profile(user_data_dir)
                driver.quit = wrapped_quit
            return driver


        # Launch failed
        _print_log(f"Attempt {attempt} failed: {(error_msg or 'unknown')[:300]}", "ERROR")

        # =============================================
        # SELF-HEALING: Parse error for correct version
        # =============================================
        if error_msg:
            extracted_version = _extract_version_from_error(error_msg)
            if extracted_version and extracted_version != version_to_use:
                _print_log(f"SELF-HEALING: Switching from {version_to_use} to {extracted_version}")
                version_to_use = extracted_version
                purge_stale_chromedriver()
            elif attempt == 2 and version_to_use:
                version_to_use = version_to_use - 1
                _print_log(f"Trying version_main={version_to_use} (decrement)")
                purge_stale_chromedriver()
            elif attempt >= 3:
                version_to_use = None
                _print_log("Falling back to full auto-detect mode.")
                purge_stale_chromedriver()
            else:
                purge_stale_chromedriver()
        else:
            purge_stale_chromedriver()

        if attempt < max_retries:
            # ── ZOMBIE CLEANUP BETWEEN ATTEMPTS ──────────────────────────────
            # If the previous attempt launched Chrome but then crashed during
            # attachment (e.g. subprocess closure bug, driver mismatch), Chrome
            # stays alive as a zombie holding the user_data_dir lock.
            # Every subsequent attempt times out waiting for the port to open
            # because Chrome refuses to start with a locked profile.
            # Force-kill any process bound to this profile NOW.
            if user_data_dir:
                _print_log(f"Cleaning up zombie processes for profile before next attempt...")
                _kill_chrome_processes_for_profile(user_data_dir)
                _unlock_profile(user_data_dir, profile_directory)
            time.sleep(2)

    _print_log(f"CRITICAL: Failed to launch browser after {max_retries} attempts.", "ERROR")
    return None


# =============================================================================
# SECTION 5: Public Pre-warm API
# =============================================================================

def prewarm_chromedriver() -> Optional[str]:
    """
    PUBLIC API: Resolves and caches the chromedriver executable path ONCE before
    the account checking loop begins.  Call this from run_account_checks() so the
    first account does not incur the full 30-second network resolution window.

    Returns the chromedriver path if resolved, or None if resolution failed.
    The result is stored in _cached_chromedriver_path so _launch_chrome_with_timeout
    uses it immediately on the next call without any network round-trip.
    """
    global _cached_chromedriver_path
    with _chromedriver_cache_lock:
        if _cached_chromedriver_path and os.path.isfile(_cached_chromedriver_path):
            _print_log(f"[Prewarm] Chromedriver already cached: {_cached_chromedriver_path}")
            return _cached_chromedriver_path

        _print_log("[Prewarm] Resolving chromedriver path before account loop (30s timeout)...")
        _resolve_result = {"path": None, "error": None}

        def _resolve():
            try:
                import chromedriver_autoinstaller
                _resolve_result["path"] = chromedriver_autoinstaller.install()
            except Exception as _re:
                _resolve_result["error"] = str(_re)

        t = threading.Thread(target=_resolve, daemon=True)
        t.start()
        t.join(timeout=30)

        if t.is_alive():
            _print_log("[Prewarm] chromedriver_autoinstaller timed out. Will retry at first browser launch.", "WARNING")
            return None

        if _resolve_result["error"]:
            _print_log(f"[Prewarm] chromedriver_autoinstaller error: {_resolve_result['error']}. Searching PATH.", "WARNING")
        elif _resolve_result["path"] and os.path.isfile(_resolve_result["path"]):
            _cached_chromedriver_path = _resolve_result["path"]
            _print_log(f"[Prewarm] Chromedriver resolved and cached: {_cached_chromedriver_path}")
            return _cached_chromedriver_path

        # Fallback: search system PATH
        import shutil as _shutil
        _found = _shutil.which("chromedriver") or _shutil.which("chromedriver.exe")
        if _found:
            _cached_chromedriver_path = _found
            _print_log(f"[Prewarm] Chromedriver found in PATH: {_cached_chromedriver_path}")
            return _cached_chromedriver_path

        _print_log("[Prewarm] Could not resolve chromedriver by any method.", "WARNING")
        return None
