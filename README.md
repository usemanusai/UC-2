# UC — Undetected Checker

> **Production-grade multi-account credential validator** powered by Undetected ChromeDriver, rektCaptcha auto-solve, and a rich Tkinter GUI.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [GUI Tab Parameter Index (Complete Catalog)](#gui-tab-parameter-index-complete-catalog)
- [Advanced Core Features Manual](#advanced-core-features-manual)
  - [1. The 3-Tier CSS Locator & Fallback Engine](#1-the-3-tier-css-locator--fallback-engine)
  - [2. Automated Selector Discovery Modes](#2-automated-selector-discovery-modes)
  - [3. Behavioral Jitter System](#3-behavioral-jitter-system)
  - [4. Session Profile Seeding & Isolation](#4-session-profile-seeding--isolation)
  - [5. Claude Proxy Fallback Integration](#5-claude-proxy-fallback-integration)
  - [6. Telegram Reporting Subsystem](#6-telegram-reporting-subsystem)
  - [7. Tab Monitoring & Port Scan Daemon](#7-tab-monitoring--port-scan-daemon)
  - [8. Phase 2 Hardening Core Engine (2026 Standards)](#8-phase-2-hardening-core-engine-2026-standards)
  - [9. AI Orchestration & Extended Skill Modules](#9-ai-orchestration--extended-skill-modules)
- [Extensions & Solvers](#extensions--solvers)
- [Proxy Support](#proxy-support)
- [Results & Output](#results--output)
- [Configuration Files](#configuration-files)
- [Known Limitations](#known-limitations)
- [Changelog](#changelog)

---

## Overview

UC is a fully automated account-checking tool designed for modern, high-security login forms. It features:

1. **Undetected Chrome Integration**: Bypasses Cloudflare, DataDome, and advanced bot-detection systems by attaching to physical Chrome instances via randomized debug ports.
2. **Behavioral Human Jitter**: Simulates realistic mouse movements (Cubic Bézier curves) and keyboard input (Gaussian WPM models + cognitive hesitations).
3. **Robust Fallback Engine**: Uses a 3-tier selector matching hierarchy (Explicit -> Heuristic Dictionary -> CDP AI Self-Discovery).
4. **Isolated Sandbox Profiles**: Allocates fresh, locked directories and randomized ports per validation process, with pre-configured extensions and toolbar auto-pinning.
5. **Passive & Active Captcha Solvers**: Integrates `rektCaptcha` for browser-level captcha auto-solving, alongside custom models and a local Claude proxy bridge.

---

## Features

| Category | Feature |
|---|---|
| **Browser Kernel** | Undetected ChromeDriver (UC mode), physical headed/headless Chrome attachment, duplicate tab sweeping |
| **Stealth & Mimicry** | Parametric Bézier cursor drift, Gaussian typing WPM modeling, machine HWID rotation, custom user-agents |
| **Fallback Engine** | Explicit GUI configuration, native multithreaded heuristic dictionary, CDP-connected AI discovery |
| **Discovery Squads** | CrewAI explorer/analyst/verifier squads, persistentheaded Rust browser 3-phase exploration loop |
| **Session & Sandbox** | Same-session reuse, per-account temp profiles, Preferences file write-ahead seeding, toolbar pin synchronization |
| **Captcha Solvers** | rektCaptcha CRX auto-patching, Moodle and Shaparak resolvers, OCR cache database, local Claude completions proxy |
| **Proxy Routing** | HTTP/HTTPS/SOCKS5, round-robin, random, and single-proxy mapping |
| **Log Ingestion** | Bulk log importer matching password files with cookies, per-account SQLite ingestion |
| **Telemetry & Alerts** | Real-time entropy uniqueness monitor, Telegram bot messenger with 4000-char safety clamping, CDP tab scanner |

---

## Architecture

```
accounts_checker_builder-main/
├── validator_pro_v2.py          # Main entry point — GUI + orchestration
├── _ext_unpacked/               # Unpacked Chrome extensions loaded dynamically at runtime
│   ├── Reviews-rektCaptcha-reCaptcha-Solver_3d2008aa/     # rektCaptcha auto-solver extension
│   ├── Moodle-Eacads-Captcha-Solver-Chrome-Web-Store_6a627938/ # Moodle CAPTCHA solver extension
│   └── Shaparak-Captcha-Solver-Chrome-Web-Store_fdd1e877/     # Shaparak CAPTCHA solver extension
├── engine/
│   ├── kernel/
│   │   ├── browser_factory.py   # Chrome launch, retry logic, zombie cleanup
│   │   ├── heuristics.py        # 80+ CSS fallback heuristics & error patterns
│   │   └── math_engine/         # Core 2026 mathematical hardening engine
│   │       ├── crypto.py        # TPM 2.0 & DPAPI zero-trust credentials encryption
│   │       ├── entropy.py       # Tsallis entropy & KL divergence fingerprint uniqueness validation
│   │       ├── langevin.py      # Langevin trajectory modeling for human-mimicking cursor movement
│   │       ├── scheduler.py     # Earliest Deadline First (EDF) heapq-based priority task scheduler
│   │       ├── state.py         # Vector Clocks and lock-free DB concurrency helpers
│   │       ├── tda.py           # Zhang-Shasha Tree Edit Distance & L2C2 continuity checks
│   │       └── verification.py  # Z3 formal action verification and semantic analysis
│   ├── core/
│   │   ├── cleanup_daemon.py    # Cleans zombie browser files and processes
│   │   ├── discovery_bridge.py  # Bridge between discovery squad & local kernel
│   │   ├── discovery_schema.py  # Pydantic schemas for AI discovery results
│   │   └── proxy_worker.py      # Dynamic proxy rotator and crawler threads coordinator
│   ├── registry/
│   │   ├── configs/
│   │   │   └── default.txt      # Default layout settings
│   │   ├── settings.json        # Encrypted Tkinter UI configuration settings
│   │   ├── settings.json.bak    # Backup of encrypted configuration settings
│   │   ├── last_working_model.txt # Cached name of the last successful LLM model
│   │   └── discovery_results.db # SQLite database of discovered/cached selectors
│   └── reporting/
│       ├── csv_exporter.py      # SQLite database log-to-CSV report generator
│       └── test_csv_exporter.py # Unit tests for CSV log exporting
├── configs/                     # Pre-built site CSS selector presets (Gmail, Honey, Digiseller, Pastebin)
├── ai_captcha/                  # Claude AI-powered CAPTCHA solver and HTTP proxy bridge
│   ├── claude_proxy_bridge.py   # OCR-based CAPTCHA resolver API bridge
│   ├── test_claude_proxy_bridge.py # Unit tests for Claude proxy bridge
│   ├── captcha_dispatcher.py    # Third-party CAPTCHA API routing hub
│   ├── anticaptcha_api.py       # Anti-Captcha API wrapper
│   ├── capsolver_api.py         # Capsolver API wrapper
│   ├── twocaptcha_api.py        # 2Captcha API wrapper
│   └── ocr_results.txt          # OCR cache file of solved CAPTCHAs
├── agents/                      # CrewAI orchestration and agent workflows
│   ├── free_browser_automation_enhancement_squad_v1_crewai-project/
│   └── infrastructure/          # Supporting orchestration configurations
├── agent-browser/               # Browser exploration/discovery skill module
│   └── SKILL.md                 # Skill specification markdown
├── discovery_squad/             # Headless TypeScript-based discovery agent
├── web-reader/                  # Web scanning and scraping skill module
├── web-search/                  # Google/DDG web search skill module
├── browser_reinstaller.py       # One-click Chrome reinstall utility and HWID spoofer
├── extension_configurator.py    # CDP-based extension runtime configurator
├── human_jitter.py              # Keystroke timing humanizer
├── locator.py                   # Cross-platform path resolver
├── network_stealth.py           # Network fingerprint stealth patches
├── session_isolation.py         # Per-account Chrome profile isolation manager
├── tab_monitor.py               # Active tab monitor and port scanner
├── main_interface.py            # Legacy main launcher GUI (optional/obsolete)
└── requirements.txt             # Python dependencies
```

---

## Requirements

- **OS:** Windows 10/11 (x64)
- **Python:** 3.10 – 3.13
- **Google Chrome:** Version 120+ (matching chromedriver auto-downloaded)
- **RAM:** 4 GB minimum, 8 GB recommended (each Chrome instance uses ~300 MB)

> **Mandate:** The project strictly follows a 'Zero Software Cost, Self-Hosted First' mandate. Dependencies must be restricted to the existing `requirements.txt` unless a minimal addition is heavily justified, and paid third-party dependencies or cloud-only features are prohibited.

Install dependencies with:
```bash
pip install -r requirements.txt
```

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/usemanusai/UC.git
cd UC

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch
python validator_pro_v2.py
```

---

## Quick Start

1. **Run** `python validator_pro_v2.py`.
2. Set **Website Target Link** (e.g., `https://example.com/login`) and **Website Valid Link** (e.g., `dashboard` or `welcome`).
3. Paste credentials in `email:password` format into the **Account Inputs** box at the bottom.
4. Set the selectors for email, password, and submit, or use the **✨ Auto-Discover** feature in the stealth tab.
5. Click **Check Accounts**.

---

## GUI Tab Parameter Index (Complete Catalog)

The Tkinter GUI is organized into 11 specialized configuration tabs:

### 1. REQUIRED: General Settings
- **Lock UI Toolbar**: Buttons to manage editing/dragging state.
  - `🔒 Lock`: Prevents dragging of sequence blocks and disables editing of the fields.
  - `🔓 Unlock`: Enables field editing and drag-and-drop block reordering.
  - `↺ Reset to Default`: Rebuilds the sequence back to the default 11 fields.
  - `🎨 Theme Colors`: Opens the color customizer dialog to modify the dark-theme UI palettes.
  - `➕ Add Workflow Rule`: Opens the custom workflow step builder (which natively supports custom post-login macro actions):
    - **Step Types**: `Custom Navigation`, `Text Input` (maps to `custom_text`), `Click Element` (maps to `custom_click`), `Sleep Delay` (maps to `custom_sleep`), `AI Content Generation`, and `Data Saving`.
    - **Step Label**: Customized name for the generated block.
- **Draggable Field Sequence Blocks**:
  - `Website Target Link (Required)`: The starting entrypoint login URL.
  - `Website Valid Link (Required)`: String fragment matching post-login URLs upon success.
  - `Redirect URL (Optional)`: Matches specific intermediate redirect paths.
  - `CSS Selector for Email / Username (Required)`: Selector for target input field.
  - `CSS Selector for Next Button (Optional)`: Multi-step transition click selector.
  - `Sleep Duration (Email)`: Time (0-100s) to wait for page to transition after inputting email.
  - `CSS Selector for Password (Required)`: Selector for password input.
  - `CSS Selector for Next Button Password (Optional)`: Click selector for intermediate password page.
  - `Sleep Duration (Password)`: Time (0-100s) to wait for transition after inputting password.
  - `CSS Selector for Submit / Login Button (Required)`: Selector for final submit.
  - `Sleep Duration (Submit)`: Time (0-100s) to wait after submit to confirm success or failure.

### 2. REQUIRED: Capture Settings
- **Enable Inner HTML Capture**: Checkbutton (boolean). Captures the raw inner text content of the page post-login.
- **Enable Outer HTML Capture**: Checkbutton (boolean). Captures the raw outer HTML string of the target page.
- **Capture Screenshot**: Checkbutton (boolean). Triggers a full-window screen capture upon finding a valid account.
- **CSS Selectors to Capture**: Input list and `+ Add CSS Selector` button. Dynamically appends input boxes to capture specific DOM texts (e.g., `span.account-balance`, `div.user-role`).
- **Redirect Link (Optional)**: Entry. Navigates the browser to this page (e.g., `/profile`) post-login before taking screenshots or capturing html.
- **Telegram Notifications**:
  - **Enable Telegram Notifications**: Checkbutton (boolean).
  - **Bot Token**: Entry. The unique Telegram bot API key.
  - **Chat ID**: Entry. Chat/channel ID for incoming messages.

### 3. REQUIRED: Invalid Account
- **Check this and Enable Invalid Account Checks**: Checkbutton (boolean). Must be checked to enable validation failure logic.
- **Redirect Detection URL**: Entry. Matches URL redirect paths indicating login failure.
- **Error/Alert Detection CSS Selector**: Entry. Target error alert alert container.
- **Inner HTML Text**: Entry. Case-insensitive error text patterns indicating failure.
- **Outer HTML Text**: Entry. Tag fragment structures indicating failure.

### 4. OPTIONAL: CAPTCHA Incorrect is Recheck
- **Enable CAPTCHA Incorrect Checks**: Checkbutton (boolean). Triggers a recheck/retry loop if login fails due to CAPTCHA issues.
- **Redirect Detection URL**: Entry. Match URL on CAPTCHA block redirects.
- **Error/Alert Detection CSS Selector**: Entry. Target CAPTCHA alert box.
- **Inner HTML Text**: Entry. Captcha error strings (e.g., "Verification failed", "invalid captcha").
- **Outer HTML Text**: Entry. Captcha outer container tags.

### 5. OPTIONAL: Proxy Settings
- **Enable Proxy**: Checkbutton (boolean).
- **Proxy Type**: Dropdown. Select `HTTP`, `HTTPS`, or `SOCKS5`.
- **Proxy Mode**: Dropdown. Select `Static Proxies` or `Rotating Proxies`.
- **Import Proxies from File**: Button. Loads newline-separated proxy files.

### 6. REQUIRED: Browser Settings
- **Clean Browser Data After Each Account Check**: Checkbutton (boolean). Clears local storage, cookies, and cache between accounts.
- **Use Same Browser Session for All Accounts**: Checkbutton (boolean). Keeps Chrome open across checks (speeds up process).
- **Run Browser in Incognito Mode**: Checkbutton (boolean). Appends `--incognito` flag.

### 7. OPTIONAL: Advanced Settings
- **Load Chrome Extensions**: Checkbutton (boolean). Unpacks and injects extensions from the `chrome_extensions/` folder.
- **Disable Browser Notifications**: Checkbutton (boolean). Appends `--disable-notifications`.
- **Disable Infobars**: Checkbutton (boolean). Appends `--disable-infobars`.
- **Start Browser Maximized**: Checkbutton (boolean). Appends `--start-maximized`.
- **Disable Browser Extensions**: Checkbutton (boolean). Blocks all extension loading.
- **Run Browser in Headless Mode**: Checkbutton (boolean). Runs Chrome without a visible window.
- **Use Custom User Agents**: Checkbutton (boolean). Enables user-agent rotation.
- **Select User Agents File**: Button. Loads list of user agents.
- **Use Databases**: Checkbutton (boolean). Saves history to SQLite databases.

### 8. OPTIONAL: Chromedriver Arguments
- **Add Chromedriver Argument**: Entry. User-supplied Chrome command-line switch.
- **Add Argument Button**: Appends switch to the active list.
- **Current Chromedriver Arguments Listbox**: Displays all loaded parameters.
- **Remove Selected Argument(s) Button**: Deletes arguments from execution.

### 9. OPTIONAL: Mouse Click Automation
- **Enable Mouse Click Automation**: Checkbutton (boolean).
- **Coordinate-based Clicks**: Table of `(X, Y)` coordinate buttons. Simulates native mouse clicks.
- **CSS Selector-based Clicks**: Table of CSS selectors. Clicks elements upon page loading.

### 10. VALIDATOR PRO: Stealth & AI
- **Enable Isolated Sessions (Unique Ports & Directories)**: Checkbutton (boolean). Assigns random high ports and sandboxed profile directories.
- **Enable Developer Mode for Extensions**: Checkbutton (boolean). Pre-authorizes unpacked extensions in `Preferences` to bypass security warnings.
- **Enable Kernel-Level Purge (AppData wiping)**: Checkbutton (boolean). Cleans all browser artifacts on major blocks.
- **Enable HWID Subsystem Spoofing**: Checkbutton (boolean). Alters registry/MachineGuid values.
- **Enable Persona Jitter (Bézier Mimicry)**: Checkbutton (boolean). Enables human-like movements and WPM typing intervals.
- **OpenRouter API Key(s)**: Entry. API key for selector auto-discovery and CAPTCHA solving.
- **AI Vision Model**: Combobox. Target OpenRouter LLM.
- **Claude Proxy Fallback**:
  - **Use Claude Proxy as fallback**: Checkbutton (boolean). Routes completions to localhost.
  - **Claude Proxy URL**: Entry. Local proxy API URL.
  - **Claude Proxy Model**: Entry. Proxy model definition.
- **Live Entropy Monitor**: Canvas showing browser fingerprint entropy graph.
- **Proxy List File**: Entry and `Browse...` button.
- **Cookie List File**: Entry and `Browse...` button. Inject cookies via CDP.
- **Automated Log Ingestion Engine**:
  - **Enable Automated Log Ingestion**: Checkbutton (boolean). Matches accounts to SQLite cookies.
  - **Auto-enable Session Isolation**: Checkbutton (boolean).
  - **Bulk Import from Logs Folder**: Button. Recursively scans stealer logs folders containing `Passwords.txt` and sibling `Cookies.json`.

### 11. Configuration Menu
- **Create New Config**: Setup new site presets.
- **Import Config**: Load preset `.txt` parameters.
- **Export Config**: Export current UI configuration.
- **Save Config State**: Persist GUI settings in settings registry.
- **Reset to Default**: Reset all GUI settings.

---

## Advanced Core Features Manual

### 1. The 3-Tier CSS Locator & Fallback Engine

To maximize success, UC does not rely solely on user-configured selectors. If an element is missing, it runs a 3-tier matching process:

```
[UI/Config Selector] (Tier 1)
       │
       ▼ (if fails)
[Native Heuristic Scan] (Tier 2) -> Scans 80+ common patterns and error phrases
       │
       ▼ (if fails)
[CDP AI Self-Discovery] (Tier 3) -> Connects to browser port, fetches DOM, queries LLM
```

#### Tier 1: Explicit Configuration
Runs a recursive frame-switching search (`_find_element_in_frames`) to locate elements inside any nested iframes.

#### Tier 2: Native Heuristic Scan (<2 seconds)
Directly searches for elements using a massive built-in dictionary:
- **Email Field**: 80+ CSS selectors including common ID variations, names, types, multi-language placeholder attributes (English, Russian, German, French, Chinese, Japanese, Korean, Arabic, etc.), aria-labels, and data-testids.
- **Password Field**: 80+ CSS selectors verifying `type="password"`, current-password autocompletes, and multi-language placeholders.
- **Submit / Next Buttons**: 100+ selectors targeting primary/login class names, action attributes, and value mappings.
- **Error Alerts**: Loops through `_ERROR_CSS_SELECTORS` and checks texts against `_ERROR_TEXT_PATTERNS` (covering 140+ expressions in over 30 languages). If found, it parses the DOM tree using a custom JavaScript `TreeWalker` to extract details and auto-saves the discovered selector back to the GUI.

#### Tier 3: CDP AI Self-Discovery (30–60 seconds)
Extracts the browser's active CDP port, connects via `agent-browser --cdp <port>`, takes an interactive accessibility tree snapshot, executes a custom JS evaluation query to gather up to 150 DOM elements, and queries OpenRouter/Claude proxy. The AI selects the target ref (e.g. `@e3`) or CSS selector and interacts with the page in real-time. The discovered selector is then synced back to the GUI.

---

### 2. Automated Selector Discovery Modes

Located in the **Stealth & AI** tab, this subsystem automatically discovers selectors for new websites:

#### A. Standard (AI Crew) Mode
- **Execution**: Runs in a background thread.
- **Orchestration**: Extracts the raw HTML, cleans and tokenizes it down to the most critical interactive elements (inputs, buttons, select, errors) capping at 150 tags.
- **Schema Validation**: Sends the clean DOM payload to OpenRouter or the Claude proxy. The response is parsed and validated using a Pydantic v2 `DiscoveryResult` model. This model verifies that no hallucinated markup (like URLs or code blocks) is present and ensures at least one primary login element exists.
- **Cache Database**: Discovered selectors are saved to `engine/registry/discovery_results.db`. The cache is valid for 7 days.

#### B. Rust Agent-Browser Mode
- **Interactive exploration**: Prompts the user for test credentials via a custom modal dialog.
- **Initialization**: Automatically cleans up zombie browser instances and loads required Chrome extensions (e.g., `rektCaptcha` solver) by reading paths from the `_ext_unpacked/` directory.
- **CDP Session Loop**: Spawns a headed/headless Chromium window under a persistent session name (`discovery_session`). It executes a 3-Phase Step Loop:
  - **Phase A (Action)**: Executes browser movement commands (`open`, `fill`, `click`, `wait`) chained with `&&` to keep the daemon session alive.
  - **Phase B (Snapshot)**: Takes a JSON accessibility tree snapshot using the custom Rust `snapshot -i` command.
  - **Phase C (Eval)**: Runs a custom JS query to gather detailed interactive element metadata.
- **Decision Engine**: Sends the current page state, accessibility tree, and execution history to the AI to determine the next logical action (e.g., waiting for captcha solver, filling fields, clicking buttons) until login completes or fails. The extracted selectors are then loaded into the GUI.

---

### 3. Behavioral Jitter System

Located in `human_jitter.py`, this module replaces standard Selenium macros with human-like interactions:

- **AI Personas**: Supported profiles include:
  - `systematic_researcher`: Typified by slow typing speeds (60 WPM), high cognitive hesitations, smooth cursor curves (low offsets), and careful scrolling.
  - `frustrated_user`: Fast typing speed (100 WPM), low hesitation, sharp cursor curves (high offsets), and fast, aggressive scrolling.
- **Cubic Bézier Cursor Paths**: Generates natural mouse paths between coordinate points `P0` and `P3` using two randomized control points `P1` and `P2` influenced by the active persona. Pauses between mouse increments are randomized between 1ms and 5ms.
- **Keystroke Simulator**: Simulates human typing speeds based on WPM. Character delays vary from 50% to 250% of the calculated WPM base delay, interspersed with randomized cognitive pauses (200–500ms) on a percentage of characters.
- **Non-linear scrolling**: Scrolls pages using JavaScript `window.scrollBy` divided into random increments. The step delay and scroll size adjust dynamically according to the persona's `scroll_aggressiveness` factor.

---

### 4. Session Profile Seeding & Isolation

Located in `session_isolation.py`, this subsystem isolates profiles to prevent account tracking:

- **Unique Directory & Port Allocation**: Allocates isolated profile directories (`temp_sessions/session_XXXXXXXX/`) and checks localhost sockets to bind unique ports (15000–25000) for debugger connections.
- **Preferences Seeding**: Prior to launching Chrome, writes a raw JSON `Preferences` file into the new profile folder (specifically under `Profile 1`). This seeds `has_seen_welcome_page=True` and `developer_mode=True` to prevent the initial Welcome wizard and enable extension loading. Stealth pre-injection of cookies, proxies, and user-agents into Chrome sessions is achieved using dynamic, unpacked Chrome extensions leveraging standard APIs (`chrome.cookies`, `chrome.proxy`, `chrome.declarativeNetRequest`) rather than `chrome.debugger`, to avoid triggering Chrome's debugging infobar.
- **Verified Extension ID Parsing**: Unpacked extensions require stable IDs to execute scripts in cross-origin frames. The manager reads `_metadata/verified_contents.json`, decodes the JWS signed-content payload via Base64url, and extracts the authentic Chrome Web Store `item_id` (falling back to MD5 if missing).
- **Toolbar Pinning**: Invokes `extension_configurator.py` to pin the loaded extension IDs directly in Chrome's internal registry, ensuring their icons are visible on the toolbar from the start.
- **Stale Cleanups**: Performs directory garbage collection on startup, deleting profiles older than 3600 seconds.

---

### 5. Claude Proxy Fallback Integration

Located in `ai_captcha/claude_proxy_bridge.py`, this module routes AI solver calls through a local proxy:

- **Proxy Endpoint**: When the local proxy toggle is checked (or no OpenRouter keys are set), requests are sent to `http://localhost:8080/v1/chat/completions`.
- **API Payload Matching**: Translates standard OpenRouter requests to local proxy-compatible formats, swapping model identifiers to local models (e.g. `gemini-3-flash`) and bypassing authorization.
- **Health Verification**: Periodically pings `http://localhost:8080/health` using `httpx` (falling back to `requests` if missing) to check proxy availability.
- **OCR Cache**: Captcha solutions are saved in `ai_captcha/ocr_results.txt` to prevent duplicate API requests for identical captcha challenges.

---

### 6. Telegram Reporting Subsystem

Sends notifications to a Telegram channel or chat:

- **Custom Formatting**: Formats valid credentials and captures fields into a structured notification message.
- **Filter large payloads**: Automatically excludes large HTML payloads (`inner_html`, `outer_html`) to keep messages readable.
- **Safety Clamping**: Messages are capped at 4000 characters (Telegram limit: 4096) to prevent API payload errors.

---

### 7. Tab Monitoring & Port Scan Daemon

Located in `tab_monitor.py`, this utility runs alongside the checker to monitor active sessions:

- **Brute-Force Scanner**: Scans localhost ports (10000–20000) in chunks of 500. It queries `/json/version` to locate active Chrome CDP ports.
- **Tab Monitoring**: Connects to the active port's `/json` endpoint every 500ms, logging the URLs, page titles, and IDs of all open pages to `tab_monitor.log`.

### 8. Phase 2 Hardening Core Engine (2026 Standards)

Implemented under `engine/kernel/math_engine/`, this subsystem mathematically and cryptographically hardens the browser and network profile to resist advanced 2026 bot fingerprinting and coordinate hijacking:

#### A. Registry HWID Rotation & Isolation (`browser_reinstaller.py`, `session_isolation.py`)
- **Vector Clock Coordination**: Prevents concurrency collisions during Windows `MachineGuid` and `DigitalProductId` rotation across multi-node execution engines. Uses the `VectorClock` class inside `state.py` mapped to a WAL SQLite state database (`sessions_registry.db`).
- **Thermodynamic Fingerprint Divergence Filtering**: When generating a synthetic hardware profile, `browser_reinstaller.py` executes up to 5 generation loops, verifying each profile against an organic reference distribution. It accepts the profile only when the Kullback-Leibler (KL) divergence computed via `verify_fingerprint_entropy` in `entropy.py` is strictly below `0.55`.
- **AppData Wiping**: Purges Chrome's tracking directories (`Cache`, `Code Cache`, `IndexedDB`, `Service Worker`, and fingerprint files) on startup to guarantee hard isolation.
- **Registry State & Session DB Encryption**: Cryptographically seals profile directories (`data_dir`) and logical clocks (`clock_json`) stored in `sessions_registry.db` using AES-GCM authenticated encryption (base64-encoded).

#### B. Topological DOM Selector Fallback (`tda.py`, `validator_pro_v2.py`)
- **ZSS Tree Edit Distance**: If standard XPath or CSS selectors fail or timeout (such as under dynamic class obfuscation), parses the target page's DOM subtree using an HTML Parser into a `DOMNode` tree structure.
- **Topological Matching**: Compares all visible candidate elements against a reference `DOMNode` template and selects the candidate with the minimum tree edit distance using a pure-Python implementation of the **Zhang-Shasha (ZSS) Tree Edit Distance (TED)** algorithm. Integrated into the Selenium lookup wrapper `_safe_find_element` in `validator_pro_v2.py`.

#### C. Lipschitz Mouse Jitter Constraint (`tda.py`, `validator_pro_v2.py`)
- **L2C2 Spatially-Local Regularization**: Verifies that the spatial movement coordinates ($dx, dy$) of simulated mouse clicks are bounded by the structural edit distance of the DOM ($d_{dom}$) using a Lipschitz continuity constraint:
  $$d_{spatial} \le L \cdot d_{dom}$$
- **Clickjacking Protection**: Integrated into the pyautogui click execution loop in `validator_pro_v2.py` to prevent coordinate-based hijack attacks by validating local continuity bounds dynamically.

#### D. Zero-Trust Cryptography (`crypto.py`, `validator_pro_v2.py`)
- **TPM 2.0 & DPAPI Wrapper**: Cryptographically seals master decryption keys with TPM 2.0 silicon (with a robust fallback to DPAPI + Hardware Fingerprinting incorporating processor features, volume serials, and nodes).
- **Argon2id Key Derivation**: Employs the memory-hard KDF (`derive_key_argon2id`) dynamically scaled to system physical RAM (up to 64 MiB/65536 KB on systems >8GB RAM, falling back to 32 MiB on >4GB and 19 MiB minimum on lower memory sizes) to derive AES-GCM encryption keys.
- **Disk Storage Protection**: All local settings files (`settings.json` in `engine/registry/`) are fully encrypted using AES-GCM on disk.

#### E. Earliest Deadline First Asynchronous Scheduler (`scheduler.py`, `validator_pro_v2.py`)
- **EDF Ready Queue**: Manages asynchronous check threads using a binary min-heap (`heapq`) in `EDFScheduler`.
- **Dynamic Prioritization**: Staggers account validation tasks. Accounts matching premium/VIP patterns are dynamically prioritized (relative deadline of 1.0s and high priority) to execute before standard staggered accounts (which run with staggered deadlines and lower priority). Provides thread-safe queue lock coordination.

---

### 9. AI Orchestration & Extended Skill Modules

This section covers the auxiliary modules, prebuilt configs, and advanced agentic features packed into UC.

#### A. AI CAPTCHA Solver (`ai_captcha/`)
- **CaptchaDispatcher**: CAPTCHA solving architecture centers around `ai_captcha/captcha_dispatcher.py` (`CaptchaDispatcher`), which acts as a routing hub for integrating numerous optional 3rd-party solving APIs (Capsolver, 2Captcha, Anti-Captcha, CapMonster, NopeCHA, azapi, CaptchaAI) using their official structures.
- **Local-First Stats Dashboard**: Includes an offline dashboard (`engine/registry/gui_captcha_stats.py`) to visualize total requests, successful solves, and failed solves broken down by 3rd party solver service, managed by a thread-safe `CaptchaStatsManager`.
- **OCR-based Resolution**: Integrates locally executed OCR-based CAPTCHA solving modules.
- **Claude Proxy Bridge**: Uses `claude_proxy_bridge.py` to route visual/textual CAPTCHAs to Anthropic models, matching API payloads to local endpoints, verifying server health via `/health`, and caching results in `ocr_results.txt` to minimize API costs.

#### B. Configuration Templates (`configs/`)
- **Prebuilt Target Presets**: Ready-to-use site configuration presets for popular target platforms.
- **Prebuilt Configs Included**:
  - `gmail_config.txt`: Configured for Google/Gmail accounts.
  - `honey_config.txt`: Configured for Honey extension logins.
  - `my_digiseller_com.txt`: Configured for Digiseller.
  - `pastebin_config.txt`: Configured for Pastebin.

#### C. CrewAI & Discovery Squads (`agents/` & `discovery_squad/`)
- **Autonomous Discovery**: Orchestrates agent squads using CrewAI to scrape pages, explore DOM trees, and identify form selectors.
- **Rust Agent-Browser Execution**: Employs a custom headed/headless Chromium-based discovery loop that performs multi-phase execution (commands, snapshots, JS queries) to systematically find elements on high-security pages.

#### D. Standardized Skill Modules

##### I. Web Reader Module (`web-reader/`)
The Web Reader module extracts, parses, and formats content from any URL using the `page_reader` function from `jaegis-sdk`. It is used exclusively in backend applications.

###### 1. CLI Usage
For quick scraping tasks, you can query pages directly from the command line:
```bash
# Basic content extraction
jaegis function --name "page_reader" --args '{"url": "https://example.com"}'

# Export content directly to a JSON file
jaegis function -n page_reader -a '{"url": "https://example.com/article"}' -o page_content.json
```
CLI parameters include:
- `--name, -n` (Required): Function name, set to `"page_reader"`.
- `--args, -a` (Required): JSON arguments object containing `"url"`.
- `--output, -o` (Optional): Target JSON output path.

###### 2. Response Fields & Structures
The resulting JSON response contains the following data structure:
```typescript
{
  title: string;           // Extracted page title
  url: string;             // Original parsed URL
  html: string;            // Cleaned main article content HTML
  publishedTime?: string;  // Publication date (if found)
  text?: string;           // Optional plain text translation
  metadata: {              // Extended metadata object
    author?: string;
    description?: string;
    keywords?: string[];
  }
}
```

###### 3. SDK Integration Examples
**Basic Page Scraping:**
```javascript
import JAEGIS from 'jaegis-sdk';

async function readWebPage(url) {
  const zai = await JAEGIS.create();
  const result = await zai.functions.invoke('page_reader', { url });
  console.log('Title:', result.data.title);
  console.log('HTML Content:', result.data.html);
  return result.data;
}
```

**Advanced Web Content Analyzer (with Caching & Word Estimation):**
```javascript
import JAEGIS from 'jaegis-sdk';

class WebContentAnalyzer {
  constructor() {
    this.cache = new Map();
  }

  async initialize() {
    this.zai = await JAEGIS.create();
  }

  async readPage(url, useCache = true) {
    if (useCache && this.cache.has(url)) {
      return this.cache.get(url);
    }
    const result = await this.zai.functions.invoke('page_reader', { url });
    if (useCache) this.cache.set(url, result.data);
    return result.data;
  }

  estimateWordCount(html) {
    const text = html.replace(/<[^>]*>/g, ' ');
    return text.split(/\s+/).filter(word => word.length > 0).length;
  }
}
```

**Express.js API Endpoint Integration:**
```javascript
import express from 'express';
import JAEGIS from 'jaegis-sdk';

const app = express();
app.use(express.json());
let zai;

app.post('/api/read-page', async (req, res) => {
  try {
    const { url } = req.body;
    const result = await zai.functions.invoke('page_reader', { url });
    res.json({ success: true, data: result.data });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});
```

###### 4. Best Practices & Security
- **Backend Lock**: The module must never run on client-side environments to protect API configurations.
- **Rate Limiting**: Implement exponential backoff or scheduling wrappers (like `node-cron`) to throttle high-volume scraping loops.
- **HTML Sanitization**: Always sanitize extracted raw HTML content before displaying it in your UI to prevent Cross-Site Scripting (XSS).

---

##### II. Web Search Module (`web-search/`)
The Web Search module queries the web for real-time information, returning structured results with metadata. It utilizes the `web_search` function.

###### 1. CLI Usage
```bash
# Basic keyword search
jaegis function --name "web_search" --args '{"query": "latest news"}'

# Search with custom constraints (number of results, recency filtering)
jaegis function -n web_search -a '{"query": "AI research", "num": 5, "recency_days": 7}' -o results.json
```
CLI arguments inside `--args`:
- `query` (string, required): Keyword query.
- `num` (number, optional): Result limit count (defaults to 10).
- `recency_days` (number, optional): Recency filter.

###### 2. Response Structure
Each search result maps to the following TypeScript interface:
```typescript
interface SearchFunctionResultItem {
  url: string;          // Full URL of the result
  name: string;         // Page title
  snippet: string;      // Preview text description
  host_name: string;    // Domain name
  rank: number;         // Result position rank
  date: string;         // Publication/update date
  favicon: string;      // Favicon URL
}
```

###### 3. SDK Integration Examples
**Basic Search:**
```javascript
import JAEGIS from 'jaegis-sdk';

async function searchWeb(query) {
  const zai = await JAEGIS.create();
  const results = await zai.functions.invoke('web_search', { query, num: 10 });
  return results; // Array of SearchFunctionResultItem
}
```

**Advanced Search & Summarize (with AI completions):**
```javascript
import JAEGIS from 'jaegis-sdk';

async function searchAndSummarize(query) {
  const zai = await JAEGIS.create();
  const searchResults = await zai.functions.invoke('web_search', { query, num: 10 });

  const context = searchResults
    .slice(0, 5)
    .map((r, i) => `${i + 1}. ${r.name}\n${r.snippet}`)
    .join('\n\n');

  const completion = await zai.chat.completions.create({
    messages: [
      { role: 'assistant', content: 'Summarize the search results clearly.' },
      { role: 'user', content: `Query: "${query}"\n\nResults:\n${context}` }
    ],
    thinking: { type: 'disabled' }
  });

  return {
    query,
    summary: completion.choices[0]?.message?.content,
    sources: searchResults.slice(0, 5).map(r => ({ title: r.name, url: r.url }))
  };
}
```

**Express.js Search API Endpoint:**
```javascript
app.get('/api/search', async (req, res) => {
  try {
    const { q, num = 10 } = req.query;
    const results = await zai.functions.invoke('web_search', { query: q, num: parseInt(num) });
    res.json({ success: true, results });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});
```

---

## Extensions & Solvers

Chrome extensions are loaded directly from the pre-unpacked subdirectories under `_ext_unpacked/` at runtime:
1. `Reviews-rektCaptcha-reCaptcha-Solver_3d2008aa` (Auto-solves reCAPTCHA v2/v3).
2. `Moodle-Eacads-Captcha-Solver-Chrome-Web-Store_6a627938` (Moodle captcha solver).
3. `Shaparak-Captcha-Solver-Chrome-Web-Store_fdd1e877` (Shaparak payment solver).

### rektCaptcha Auto-Patching
To ensure captcha solving is always active, UC modifies `background.js` during extension extraction:
- Sets `recaptcha_auto_open = true`
- Sets `recaptcha_auto_solve = true`

---

## Proxy Support

Supports multiple formats (`host:port`, `host:port:user:pass`, `socks5://host:port`) and modes:
- **Round-Robin**: Rotates through proxies sequentially.
- **Random**: Assigns a random proxy per account.
- **Single**: Applies one proxy for the entire run.

### Dynamic Proxy Fetching
UC features an **Automated Proxy Health Checker & Fetcher** (via `ProxySourceWorker`). When enabled:
- The UI accepts a `Proxy Source URL` and a configurable `Fetch Interval`.
- A background daemon automatically fetches fresh proxy lists from the remote HTTP endpoint while validation is active.
- A background daemon (`ProxySourceWorker` thread) automatically and periodically fetches fresh proxy rotators from configurable HTTP source URLs while validation is active.
- Proxies are injected into the thread-safe `ProxyRotator` dynamically, preventing failures due to stale or dead proxy nodes midway through a large batch.

---

## Results & Output

Each run creates a timestamped results directory containing:
- `valid.txt`: Valid credentials.
- `invalid.txt`: Invalid credentials.
- `unknown.txt`: Accounts that failed due to CAPTCHA or timeout issues.
- `checked.db`: SQLite history database.
- `screenshots/`: PNG screenshots of valid logins.

Users can also generate offline reports with the built-in **SQLite Log-to-CSV Report Generator**. Use the `Export DB to CSV` button under the Configuration menu to easily share and review validation logs.

---

## Configuration Files

- `engine/registry/settings.json`: Persisted GUI settings (includes AI models, CAPTCHA solver settings, and custom workflows). Fully encrypted on disk using AES-GCM and DPAPI secure hardware-bound fallbacks, saving/loading Tkinter UI states across restarts securely.
- `engine/registry/settings.json.bak`: Cryptographic backup of the GUI settings file, automatically rotated for atomic write protection.
- `engine/registry/configs/default.txt`: Pre-warm preset mapping layout configuration.
- `configs/`: Directory containing prebuilt selector configurations for target websites:
  - `my_digiseller_com.txt` (Digiseller login configuration)
  - `gmail_config.txt` (Gmail configuration)
  - `honey_config.txt` (Honey extension login configuration)
  - `pastebin_config.txt` (Pastebin login configuration)

---

## Known Limitations

- **Windows Only**: Uses Windows-specific registry calls and paths.
- **Chrome Compatibility**: Requires Google Chrome version 120+.
- **invisible CAPTCHAs**: Invisible reCAPTCHA challenges may fail if the target site detects automation.

---

## Changelog

---

## License

Private repository — all rights reserved.

---

## Development Guidelines

- **Architecture:** New core logic and class-based architecture should be placed within the `engine/` directory structure (e.g., `engine/kernel/` and `engine/core/`). AI agent configurations and journals (such as the Palette UX agent prompt) are stored in the `.Jules/` directory at the project root.
- **Testing:**
  - The project supports pytest and standard unittest for testing. Test files can be located alongside the modules they test (e.g., `test_*.py`) and can be executed using `pytest .`, `pytest <path_to_test>`, or `python -m unittest discover`. Prefix test commands with `PYTHONPATH=.` if internal module resolution fails.
  - Test cases are structured using the standard `unittest` framework (e.g., `unittest.TestCase`, `unittest.mock`) and are executed using the `pytest` runner.
  - When mocking module-level imports in tests (e.g., to stub unavailable packages like `undetected_chromedriver`), use `unittest.mock.patch.dict('sys.modules', ...)` combined with `importlib.reload()` within a pytest fixture to prevent global test state pollution.
- **Formatting:** The project uses `black` for Python code formatting, and `flake8` or `pylint` for code quality linting.
- **Schemas:** The Python codebase uses Pydantic (supporting both V1 and V2) for schema validation. It includes dictionary-based fallback logic for environments without Pydantic, and tests should be written to account for both scenarios.
- **Definition of Done:** A feature is only considered complete or 'shipped' when it is fully tested (via pytest), integrated into the UI or workflow (e.g., `validator_pro_v2.py`), and has accompanying documentation updates in the `README.md`.
- **Tkinter GUI:** Tkinter GUI modifications should prioritize accessibility (e.g., logical tab order, keyboard navigation), use existing `ttk` styling configurations, and provide clear UI feedback or error messaging.
