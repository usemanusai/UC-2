"""
extension_configurator.py — Post-launch extension setup engine.

Responsibilities:
  1. PIN all loaded extensions to the Chrome toolbar by writing the
     `extensions.toolbar` list into the profile Preferences BEFORE Chrome
     starts (pre-launch pinning via Preferences seeding).
  2. CONFIGURE extension-specific settings AFTER Chrome attaches, via CDP:
     - rektCaptcha: enable recaptcha_auto_open + recaptcha_auto_solve
     - Any future extension configs are added to EXT_CONFIGS below.

Usage (called from browser_factory.py after successful uc.Chrome() attach):
    from extension_configurator import configure_extensions_post_launch
    configure_extensions_post_launch(driver, ext_dirs)
"""

from __future__ import annotations
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Extension-specific storage configuration registry ────────────────────────
# Key   = canonical name fragment (matched against extension manifest 'name',
#         case-insensitive substring match, OR against the chrome-extension URL).
# Value = dict of chrome.storage.local key → value to set on every launch.
EXT_CONFIGS: dict[str, dict] = {
    # rektCaptcha: reCaptcha Solver  (Reviews-rektCaptcha-reCaptcha-Solver.crx)
    # Screenshot shows: Auto-Open ON, Auto-Solve ON
    # Storage keys confirmed from CRX source: background.js defaults +
    # recaptcha.js reads: n.recaptcha_auto_open / n.recaptcha_auto_solve
    "rektcaptcha": {
        "recaptcha_auto_open":  True,
        "recaptcha_auto_solve": True,
    },
}


# ─── Pre-launch: Toolbar Pinning ─────────────────────────────────────────────

def pin_extensions_in_preferences(
    prefs_path: str,
    ext_ids: list[str],
) -> None:
    """
    Writes (or merges) the `extensions.toolbar` key in a Chrome Preferences
    file so all provided extension IDs are pinned to the toolbar.

    This must be called BEFORE Chrome launches.  If the file doesn't exist yet
    it will be created with a minimal scaffold; if it does exist the toolbar
    list is merged non-destructively.

    Parameters
    ----------
    prefs_path : str
        Absolute path to the Chrome profile's Preferences JSON file.
    ext_ids : list[str]
        Extension IDs to pin.  Duplicates are removed automatically.
    """
    if not ext_ids:
        return

    prefs: dict = {}
    if os.path.isfile(prefs_path):
        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
        except Exception as e:
            logger.warning(f"[ExtConfig] Could not read Preferences for pinning: {e}")

    # Merge existing toolbar list with our ext IDs (no duplicates)
    existing_toolbar: list = (
        prefs.get("extensions", {}).get("toolbar", [])
    )
    merged = list(dict.fromkeys(existing_toolbar + ext_ids))  # preserves order, dedupes

    prefs.setdefault("extensions", {})["toolbar"] = merged

    try:
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
        logger.info(
            f"[ExtConfig] Pinned {len(merged)} extension(s) to toolbar in {prefs_path}"
        )
    except Exception as e:
        logger.warning(f"[ExtConfig] Could not write Preferences for pinning: {e}")


# ─── Post-launch: Extension Storage Configuration ────────────────────────────

def configure_extensions_post_launch(
    driver,
    ext_dirs: Optional[list[str]] = None,
    timeout: float = 20.0,
) -> None:
    """
    Called AFTER uc.Chrome() successfully attaches.  Uses CDP to:
      1. Enumerate all extension service worker targets (active + dormant).
      2. For each extension matching a rule in EXT_CONFIGS, execute
         chrome.storage.local.set({...}) in the extension's context.

    Parameters
    ----------
    driver
        The active Selenium/uc.Chrome() instance.
    ext_dirs : list[str], optional
        Unpacked extension directories (used to correlate extensions to configs).
    timeout : float
        Maximum seconds to wait/retry for extension targets.
    """
    if not EXT_CONFIGS:
        return
    try:
        _configure_extensions(driver, ext_dirs, timeout)
    except Exception as e:
        logger.warning(f"[ExtConfig] Post-launch extension configuration failed: {e}")


def _get_chrome_debug_port(driver) -> Optional[int]:
    """
    Resolve Chrome's REMOTE DEBUGGING port from the driver object.

    CRITICAL: driver._cdp_debug_port  = Chrome's --remote-debugging-port (what we need)
              driver.service.service_url port = ChromeDriver's own port  (WRONG — gives no extension info)
    """
    # Primary: stamped by browser_factory.py after attach
    port = getattr(driver, "_cdp_debug_port", None)
    if port:
        logger.debug(f"[ExtConfig] Chrome debug port from _cdp_debug_port: {port}")
        return int(port)

    # Secondary: some uc.Chrome builds expose it differently
    port = getattr(driver, "_cdp_port", None)
    if port:
        logger.debug(f"[ExtConfig] Chrome debug port from _cdp_port: {port}")
        return int(port)

    # Tertiary: parse from Selenium capabilities (set when attaching via debuggerAddress)
    try:
        caps = driver.capabilities or {}
        # Selenium 4 stores the debuggerAddress in caps
        debugger_addr = caps.get("goog:chromeOptions", {}).get("debuggerAddress", "")
        if not debugger_addr:
            debugger_addr = caps.get("se:cdp", "")
        if debugger_addr:
            import re
            m = re.search(r":(\d+)", debugger_addr)
            if m:
                port = int(m.group(1))
                logger.debug(f"[ExtConfig] Chrome debug port from caps debuggerAddress: {port}")
                return port
    except Exception:
        pass

    logger.warning("[ExtConfig] Cannot determine Chrome debug port — extension config disabled")
    return None


def _get_browser_ws_url(base_url: str) -> Optional[str]:
    """
    Retrieves the browser-level CDP WebSocket URL from /json/version.
    This browser-level WS is required for Target.getTargets which enumerates
    ALL targets including dormant service workers not listed in /json.
    """
    try:
        import requests
        resp = requests.get(f"{base_url}/json/version", timeout=4)
        ws = resp.json().get("webSocketDebuggerUrl", "")
        if ws:
            logger.debug(f"[ExtConfig] Browser WS URL: {ws}")
        return ws or None
    except Exception as e:
        logger.debug(f"[ExtConfig] Could not get browser WS URL: {e}")
        return None


def _get_all_targets(base_url: str, browser_ws_url: Optional[str]) -> list[dict]:
    """
    Enumerate ALL CDP targets (including dormant service workers) using two methods:
    1. GET /json  (active inspectable targets only)
    2. Target.getTargets via browser-level WebSocket  (ALL targets including dormant)
    Results are merged and deduplicated by target ID.
    """
    import requests
    seen_ids: set = set()
    all_targets: list[dict] = []

    # Method 1: /json HTTP endpoint
    try:
        resp = requests.get(f"{base_url}/json", timeout=3)
        for t in resp.json():
            tid = t.get("id") or t.get("targetId", "")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                all_targets.append(t)
    except Exception as e:
        logger.debug(f"[ExtConfig] /json fetch failed: {e}")

    # Method 2: Target.getTargets via browser WebSocket (catches dormant service workers)
    if browser_ws_url:
        try:
            import websocket
            ws = websocket.create_connection(browser_ws_url, timeout=5)
            try:
                ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
                raw = ws.recv()
                result = json.loads(raw).get("result", {})
                for t in result.get("targetInfos", []):
                    # Normalize field names to match /json format
                    tid = t.get("targetId", "")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        # Remap targetId → id for consistent access
                        normalized = dict(t)
                        normalized["id"] = tid
                        normalized["type"] = t.get("type", "")
                        normalized["url"]  = t.get("url", "")
                        all_targets.append(normalized)
            finally:
                ws.close()
        except Exception as e:
            logger.debug(f"[ExtConfig] Target.getTargets via browser WS failed: {e}")

    logger.debug(f"[ExtConfig] Total targets discovered: {len(all_targets)}")
    return all_targets


def _activate_service_worker(browser_ws_url: str, target_id: str) -> bool:
    """
    Send Target.activateTarget to wake up a dormant service worker.
    Returns True if the command was sent successfully.
    """
    try:
        import websocket
        ws = websocket.create_connection(browser_ws_url, timeout=5)
        try:
            ws.send(json.dumps({
                "id": 1,
                "method": "Target.activateTarget",
                "params": {"targetId": target_id}
            }))
            ws.recv()
            return True
        finally:
            ws.close()
    except Exception as e:
        logger.debug(f"[ExtConfig] Failed to activate target {target_id}: {e}")
        return False


def _inject_storage(target_ws_url: str, storage_vals: dict, ext_name: str) -> bool:
    """
    Executes chrome.storage.local.set({...}) in an extension's CDP target
    context via WebSocket Runtime.evaluate.
    Returns True on success.
    """
    import websocket
    storage_json = json.dumps(storage_vals)
    script = (
        f"new Promise((resolve) => {{"
        f"  chrome.storage.local.set({storage_json}, () => {{"
        f"    resolve('OK');"
        f"  }});"
        f"}})"
    )
    try:
        ws = websocket.create_connection(target_ws_url, timeout=8)
        try:
            # Enable Runtime
            ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            ws.recv()

            # Execute storage set
            ws.send(json.dumps({
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": script,
                    "awaitPromise": True,
                    "returnByValue": True,
                }
            }))
            raw = ws.recv()
            resp = json.loads(raw)
            exc = resp.get("result", {}).get("exceptionDetails")
            if exc:
                logger.warning(f"[ExtConfig] CDP exception setting storage for {ext_name}: {exc}")
                return False

            logger.info(f"[ExtConfig] ✓ {ext_name}: set {storage_json}")
            return True
        finally:
            ws.close()
    except Exception as e:
        logger.debug(f"[ExtConfig] Storage inject failed for {ext_name}: {e}")
        return False


def _get_or_create_target_ws(base_url: str, target_id: str, existing_ws_url: str) -> Optional[str]:
    """
    Returns a usable webSocketDebuggerUrl for a target, creating an attached
    session via Target.attachToTarget if the target doesn't have one directly.
    """
    if existing_ws_url:
        return existing_ws_url
    # Try /json/{id} to get the debugger URL
    try:
        import requests
        resp = requests.get(f"{base_url}/json/{target_id}", timeout=3)
        ws = resp.json().get("webSocketDebuggerUrl", "")
        if ws:
            return ws
    except Exception:
        pass
    # Fallback: construct URL directly (Chrome ≥112 pattern)
    return f"ws://127.0.0.1:{base_url.split(':')[-1]}/devtools/page/{target_id}"


def _configure_extensions(driver, ext_dirs: Optional[list[str]], timeout: float) -> None:
    """
    Main implementation: find extension service workers, match them to
    EXT_CONFIGS rules, and inject chrome.storage.local settings via CDP.

    Matching strategy (in order of priority):
    1. CDP target 'title' field — Chrome sets this to the extension display name
    2. Manifest 'name' field read from ext_dirs — definitive ground-truth match
    3. URL fragment — last resort, works if path contains a recognisable name
    """
    cdp_port = _get_chrome_debug_port(driver)
    if not cdp_port:
        return

    base_url = f"http://127.0.0.1:{cdp_port}"
    browser_ws_url = _get_browser_ws_url(base_url)

    # Build lookup: ext_dir_basename_lower → matched_config
    # This lets us correlate when we later discover the ext_id at runtime.
    dir_name_configs: list[tuple[str, dict]] = []
    if ext_dirs:
        for d in ext_dirs:
            manifest_path = os.path.join(d, "manifest.json")
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        mf = json.load(f)
                    mf_name = mf.get("name", "").lower()
                    mf_desc = mf.get("description", "").lower()
                    for name_frag, storage_vals in EXT_CONFIGS.items():
                        if name_frag in mf_name or name_frag in mf_desc:
                            dir_name_configs.append((mf_name, storage_vals))
                            logger.info(
                                f"[ExtConfig] Manifest match: '{mf_name}' → {list(storage_vals.keys())}"
                            )
                except Exception as me:
                    logger.debug(f"[ExtConfig] Could not read manifest from {d}: {me}")

    # Build name-to-config matching list (lowercase) from EXT_CONFIGS
    name_configs: list[tuple[str, dict]] = [(k.lower(), v) for k, v in EXT_CONFIGS.items()]

    def _matches_config(target: dict) -> Optional[dict]:
        """Return the storage config if this target matches any rule, else None."""
        # Fields to search (all lowercased)
        title   = target.get("title", "").lower()
        url     = target.get("url", "").lower()
        desc    = target.get("description", "").lower()

        for name_frag, storage_vals in name_configs:
            if name_frag in title or name_frag in url or name_frag in desc:
                return storage_vals

        # Also check against manifest names we read from ext_dirs
        for mf_name, storage_vals in dir_name_configs:
            if mf_name and (mf_name in title or mf_name in url):
                return storage_vals

        return None

    configured: set[str] = set()  # ext_ids already configured
    deadline = time.time() + timeout

    logger.info(f"[ExtConfig] Starting extension configuration on port {cdp_port} (timeout={timeout}s)")

    while time.time() < deadline and len(configured) < len(EXT_CONFIGS):
        all_targets = _get_all_targets(base_url, browser_ws_url)

        # All extension targets (any type that is chrome-extension://)
        ext_targets = [
            t for t in all_targets
            if "chrome-extension://" in t.get("url", "")
        ]

        if not ext_targets:
            logger.debug("[ExtConfig] No extension targets found yet, waiting 2s...")
            time.sleep(2)
            continue

        logger.debug(
            f"[ExtConfig] Found {len(ext_targets)} extension target(s): "
            + str([f"{t.get('type','')}:{t.get('title','')[:30]}:{t.get('url','')[:40]}" for t in ext_targets])
        )

        for target in ext_targets:
            url: str      = target.get("url", "")
            target_id: str = target.get("id") or target.get("targetId", "")
            ws_url: str   = target.get("webSocketDebuggerUrl", "")

            # Extract ext_id from chrome-extension://<id>/...
            try:
                ext_id = url.split("chrome-extension://")[1].split("/")[0]
            except IndexError:
                continue

            if ext_id in configured:
                continue

            matched_config = _matches_config(target)
            if not matched_config:
                continue

            # If dormant service_worker, activate it first
            target_type = target.get("type", "")
            if target_type == "service_worker" and browser_ws_url:
                logger.debug(f"[ExtConfig] Activating dormant service worker: {ext_id}")
                _activate_service_worker(browser_ws_url, target_id)
                time.sleep(1.5)
                # Refresh the WS URL after activation
                try:
                    import requests
                    resp = requests.get(f"{base_url}/json", timeout=3)
                    fresh = [t for t in resp.json() if t.get("id") == target_id]
                    if fresh:
                        ws_url = fresh[0].get("webSocketDebuggerUrl", ws_url)
                except Exception:
                    pass

            # Resolve WebSocket URL
            ws_url = _get_or_create_target_ws(base_url, target_id, ws_url)
            if not ws_url:
                logger.debug(f"[ExtConfig] No WS URL for {ext_id}, will retry")
                continue

            # Inject storage
            ext_label = f"{target.get('title','') or ext_id[:12]}"
            if _inject_storage(ws_url, matched_config, ext_label):
                configured.add(ext_id)
            else:
                logger.debug(f"[ExtConfig] Inject failed for {ext_label}, will retry")

        if len(configured) < len(EXT_CONFIGS):
            time.sleep(2)

    if configured:
        logger.info(f"[ExtConfig] ✓ Done — configured {len(configured)} extension(s): {configured}")
    else:
        logger.warning(
            f"[ExtConfig] ✗ No extensions configured within {timeout}s "
            f"(debug port={cdp_port}). "
            f"rektCaptcha will use its DEFAULT settings (Auto-Open=OFF, Auto-Solve=OFF)."
        )



# ─── Helper: derive ext ID from manifest key ─────────────────────────────────

def collect_ext_ids_from_dirs(ext_dirs: list[str]) -> dict[str, str]:
    """
    Returns a mapping of {extension_id: canonical_name} for all valid unpacked
    extension directories.
    """
    result: dict[str, str] = {}
    for ext_path in ext_dirs:
        manifest_path = os.path.join(ext_path, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            continue

        name = manifest.get("name", os.path.basename(ext_path))
        ext_id: Optional[str] = None

        # Method 1: derive from manifest 'key'
        key = manifest.get("key")
        if key:
            try:
                import base64, hashlib
                raw = base64.b64decode(key)
                digest = hashlib.sha256(raw).digest()
                ext_id = "".join(
                    chr(ord("a") + ((b >> 4) & 0xF)) + chr(ord("a") + (b & 0xF))
                    for b in digest[:16]
                )
            except Exception:
                pass

        # Method 2: parse _metadata/verified_contents.json
        if not ext_id:
            vc_path = os.path.join(ext_path, "_metadata", "verified_contents.json")
            if os.path.isfile(vc_path):
                try:
                    import base64 as _b64
                    with open(vc_path, "r", encoding="utf-8") as vf:
                        vdata = json.load(vf)
                    payload_b64 = vdata[0]["signed_content"]["payload"]
                    pad = 4 - len(payload_b64) % 4
                    if pad < 4:
                        payload_b64 += "=" * pad
                    payload = json.loads(
                        _b64.urlsafe_b64decode(payload_b64).decode("utf-8")
                    )
                    ext_id = payload.get("item_id")
                except Exception:
                    pass

        if ext_id:
            result[ext_id] = name

    return result
