# session_isolation.py
import socket
import random
import os
import json
import shutil
import logging
import sqlite3
import time
from typing import Tuple, Optional, Dict

# Import state management modules
from engine.kernel.math_engine.state import LockFreeStateDB, VectorClock

logger = logging.getLogger(__name__)

# IDs of Chrome's built-in component extensions that Chrome auto-installs in
# every fresh profile.  These are NOT user extensions and should be ignored.
_CHROME_COMPONENT_EXTENSION_IDS = {
    "ghbmnnjooekpmoecnnnilnnbdlolhkhi",  # Chrome Web Store Payments
    "jelniggicmclhfgnlapbkgfibmgelfnp",  # Google Chrome PDF Viewer (component)
    "lmjegmlicamnimmfhcmpkclmigmmcbeh",  # Chrome Media Router
    "nmmhkkegccagdldgiimedpiccmgmieda",  # Google Pay
}

class SessionIsolationManager:
    """
    Manages isolated browser sessions coordinating ports and profile directories
    using a lock-free SQLite WAL database, Vector Clocks, and M/G/1 throttling.
    """

    def __init__(self, base_temp_dir: str = "temp_sessions"):
        self.base_temp_dir = os.path.abspath(base_temp_dir)
        if not os.path.exists(self.base_temp_dir):
            os.makedirs(self.base_temp_dir, exist_ok=True)
            
        # Initialize central state registry database
        db_path = os.path.join(self.base_temp_dir, "sessions_registry.db")
        self.state_db = LockFreeStateDB(db_path)
        self._setup_registry_table()

    def _setup_registry_table(self) -> None:
        """Sets up the state registry schema for session coordination."""
        def init_table(conn: sqlite3.Connection):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS state_registry (
                    key TEXT PRIMARY KEY,
                    port INTEGER,
                    data_dir TEXT,
                    clock_json TEXT,
                    last_node TEXT,
                    updated_at REAL
                );
            """)
        self.state_db.run_concurrent_write(init_table)

    def create_session(self, session_id: str) -> Tuple[int, str]:
        """
        Generates and registers a unique port and data directory for a session.
        Uses optimistic WAL locking and logical Vector Clocks.
        Enforces local hardware identity integrity checks.
        """
        from engine.kernel.math_engine.crypto import verify_identity_integrity
        if not verify_identity_integrity():
            raise PermissionError("CRITICAL SYSTEM ALTERATION DETECTED: Hardware fingerprint mismatch. Aborting session creation.")

        data_dir = os.path.join(self.base_temp_dir, f"session_{session_id}")
        
        # Terminate any zombie Chrome processes using this directory first
        try:
            from engine.kernel.browser_factory import _kill_chrome_processes_for_profile
            _kill_chrome_processes_for_profile(data_dir)
        except Exception as ke:
            logger.warning(f"[Isolation] Pre-session cleanup process kill failed: {ke}")
            
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir, ignore_errors=True)
        os.makedirs(data_dir, exist_ok=True)

        # Allocate unique port and update registry state concurrently
        node_id = f"node_{socket.gethostname()}_{os.getpid()}"
        
        def register_tx(conn: sqlite3.Connection) -> int:
            from engine.kernel.math_engine.crypto import encrypt_string, decrypt_string
            # 1. Query occupied ports from registry
            cursor = conn.cursor()
            cursor.execute("SELECT port FROM state_registry")
            occupied_ports = {row[0] for row in cursor.fetchall()}
            
            # 2. Find a free port not in registry and not bound locally
            allocated_port = self._find_free_port_safe(occupied_ports)
            
            # 3. Retrieve or create Vector Clock
            cursor.execute("SELECT clock_json FROM state_registry WHERE key = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                try:
                    decrypted_clock = decrypt_string(row[0])
                    clock_data = json.loads(decrypted_clock)
                except Exception:
                    clock_data = {}
                vclock = VectorClock(node_id, clock_data)
            else:
                vclock = VectorClock(node_id)
                
            # Increment logical causality clock
            vclock.increment()
            
            # 4. Insert or replace session allocation
            encrypted_data_dir = encrypt_string(data_dir)
            encrypted_clock = encrypt_string(json.dumps(vclock.serialize()))
            conn.execute("""
                INSERT OR REPLACE INTO state_registry (key, port, data_dir, clock_json, last_node, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, allocated_port, encrypted_data_dir, encrypted_clock, node_id, time.time()))
            
            return allocated_port

        port = self.state_db.run_concurrent_write(register_tx)
        logger.info(f"[Isolation] Registered session {session_id} on port {port} at {data_dir}")
        return port, data_dir

    def cleanup_session(self, session_id: str):
        """Deletes registry records and clears session profile directory."""
        from engine.kernel.math_engine.crypto import verify_identity_integrity
        if not verify_identity_integrity():
            raise PermissionError("CRITICAL SYSTEM ALTERATION DETECTED: Hardware fingerprint mismatch. Aborting session cleanup.")

        data_dir = os.path.join(self.base_temp_dir, f"session_{session_id}")
        
        def deregister_tx(conn: sqlite3.Connection):
            conn.execute("DELETE FROM state_registry WHERE key = ?", (session_id,))
            
        try:
            self.state_db.run_concurrent_write(deregister_tx)
        except Exception as e:
            logger.warning(f"[Isolation] Failed to deregister session {session_id}: {e}")

        if os.path.exists(data_dir):
            shutil.rmtree(data_dir, ignore_errors=True)
            logger.info(f"[Isolation] Cleaned up session {session_id}")

    def _find_free_port_safe(self, occupied_ports: set) -> int:
        """Finds an available high-range port that is not in the database or bound."""
        for _ in range(100):
            port = random.randint(15000, 25000)
            if port in occupied_ports:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(('127.0.0.1', port))
                    return port
                except socket.error:
                    continue
        return 9222  # Fallback port

    def purge_all(self):
        """Purges all sessions and resets database registry."""
        from engine.kernel.math_engine.crypto import verify_identity_integrity
        if not verify_identity_integrity():
            raise PermissionError("CRITICAL SYSTEM ALTERATION DETECTED: Hardware fingerprint mismatch. Aborting session purging.")

        def clear_registry(conn: sqlite3.Connection):
            conn.execute("DELETE FROM state_registry;")
            
        try:
            self.state_db.run_concurrent_write(clear_registry)
        except Exception as e:
            logger.warning(f"[Isolation] Failed to purge database registry: {e}")
            
        if os.path.exists(self.base_temp_dir):
            # Keep the DB file, delete other directories
            for entry in os.scandir(self.base_temp_dir):
                if entry.name != "sessions_registry.db" and not entry.name.startswith("sessions_registry.db-"):
                    if entry.is_dir():
                        shutil.rmtree(entry.path, ignore_errors=True)
                    else:
                        try:
                            os.remove(entry.path)
                        except Exception:
                            pass
            logger.info("[Isolation] All sessions purged.")

    def seed_profile_for_extensions(
        self, data_dir: str, profile_directory: str = "Profile 1",
        ext_dirs: Optional[list] = None,
    ) -> None:
        """
        Writes a Chrome Preferences file into the isolated session's profile dir
        BEFORE Chrome starts, to skip the Welcome flow, enable developer_mode,
        and pre-register each extension.
        """
        if not profile_directory:
            profile_directory = "Default"

        profile_dir = os.path.join(data_dir, profile_directory)
        os.makedirs(profile_dir, exist_ok=True)
        prefs_path = os.path.join(profile_dir, "Preferences")
        if os.path.isfile(prefs_path):
            return  # Already seeded

        ext_settings = {}
        if ext_dirs:
            for ext_path in ext_dirs:
                manifest_file = os.path.join(ext_path, "manifest.json")
                if not os.path.isfile(manifest_file):
                    continue
                try:
                    with open(manifest_file, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                    ext_version = mdata.get("version", "1.0")

                    real_ext_id = None
                    verified_path = os.path.join(
                        ext_path, "_metadata", "verified_contents.json"
                    )
                    if os.path.isfile(verified_path):
                        try:
                            import base64 as _b64
                            with open(verified_path, "r", encoding="utf-8") as vf:
                                vdata = json.load(vf)
                            _payload_b64 = vdata[0]["signed_content"]["payload"]
                            _pad = 4 - len(_payload_b64) % 4
                            if _pad < 4:
                                _payload_b64 += "=" * _pad
                            _payload = json.loads(
                                _b64.urlsafe_b64decode(_payload_b64).decode("utf-8")
                            )
                            real_ext_id = _payload.get("item_id")
                        except Exception as _ve:
                            logger.debug(
                                f"[Isolation] Could not parse verified_contents.json for {ext_path}: {_ve}"
                            )

                    if not real_ext_id:
                        import hashlib as _hlib
                        real_ext_id = _hlib.md5(ext_path.encode()).hexdigest()[:32]

                    _host_perms = mdata.get("host_permissions", [])
                    _api_perms = mdata.get("permissions", [])
                    _api_perms_str = [p for p in _api_perms if isinstance(p, str)]

                    ext_settings[real_ext_id] = {
                        "active_permissions": {
                            "api": _api_perms_str,
                            "explicit_host": _host_perms,
                            "manifest_permissions": [],
                            "scriptable_host": _host_perms,
                        },
                        "from_bookmark": False,
                        "from_webstore": True,
                        "granted_permissions": {
                            "api": _api_perms_str,
                            "explicit_host": _host_perms,
                            "manifest_permissions": [],
                            "scriptable_host": _host_perms,
                        },
                        "install_time": "13000000000000000",
                        "location": 4,   # 4 = LOAD (developer-loaded unpacked)
                        "manifest": mdata,
                        "path": ext_path,
                        "state": 1,      # 1 = ENABLED
                        "version": ext_version,
                        "was_installed_by_default": False,
                        "was_installed_by_oem": False,
                    }
                except Exception as _e:
                    logger.warning(
                        f"[Isolation] Could not read manifest for {ext_path}: {_e}"
                    )

        minimal_prefs = {
            "browser": {
                "has_seen_welcome_page": True,
                "show_home_button": False,
            },
            "profile": {
                "content_settings": {"exceptions": {}},
                "exit_type": "Normal",
                "exited_cleanly": True,
            },
            "extensions": {
                "alerts": {"initialized": True},
                "last_chrome_version": "",
                "settings": ext_settings,
                "ui": {
                    "developer_mode": True,
                },
            },
            "privacy_sandbox": {"m1": {"consent_decision_made": True}},
        }

        try:
            with open(prefs_path, "w", encoding="utf-8") as fh:
                json.dump(minimal_prefs, fh, indent=2)
            logger.info(
                f"[Isolation] Seeded Preferences in {profile_dir} with {len(ext_settings)} extensions."
            )
        except Exception as e:
            logger.warning(f"[Isolation] Could not seed Preferences: {e}")

        if ext_settings:
            try:
                import sys as _sys
                _proj_root = os.path.dirname(os.path.abspath(__file__))
                if _proj_root not in _sys.path:
                    _sys.path.insert(0, _proj_root)
                from extension_configurator import pin_extensions_in_preferences as _pin
                _pin(prefs_path, list(ext_settings.keys()))
            except Exception as _pe:
                logger.warning(f"[Isolation] Toolbar pinning skipped: {_pe}")

    def _collect_all_ext_dirs(self, project_root: Optional[str] = None) -> list:
        if project_root is None:
            project_root = os.path.dirname(os.path.abspath(__file__))

        ext_dirs = []
        ext_root = os.path.join(project_root, "_ext_unpacked")
        if os.path.isdir(ext_root):
            for entry in os.scandir(ext_root):
                if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "manifest.json")):
                    ext_dirs.append(entry.path)

        chrome_ext_root = os.path.join(project_root, "chrome_extensions")
        if os.path.isdir(chrome_ext_root):
            for entry in os.scandir(chrome_ext_root):
                if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "manifest.json")):
                    if entry.path not in ext_dirs:
                        ext_dirs.append(entry.path)

        return ext_dirs

    def get_extension_load_arg(self, project_root: Optional[str] = None) -> Optional[str]:
        valid_dirs = self._collect_all_ext_dirs(project_root)
        if not valid_dirs:
            return None
        return "--load-extension=" + ",".join(valid_dirs)

    def get_isolated_session(
        self,
        account_identifier: str,
        load_extensions: bool = False,
        profile_directory: str = "Profile 1",
        project_root: Optional[str] = None,
    ) -> dict:
        """
        Returns a dict with 'port', 'dir', and 'ext_arg' keys.
        Uses SQLite WAL state registry and Vector Clocks to assert transactional safety.
        """
        if not profile_directory:
            profile_directory = "Default"

        import hashlib
        safe_id = hashlib.md5(account_identifier.encode("utf-8")).hexdigest()[:12]
        port, data_dir = self.create_session(safe_id)

        if project_root is None:
            project_root = os.path.dirname(os.path.abspath(__file__))

        ext_arg = None
        if load_extensions:
            ext_dirs = self._collect_all_ext_dirs(project_root)
            self.seed_profile_for_extensions(
                data_dir, profile_directory, ext_dirs=ext_dirs if ext_dirs else None
            )
            if ext_dirs:
                ext_arg = "--load-extension=" + ",".join(ext_dirs)

        return {"port": port, "dir": data_dir, "ext_arg": ext_arg}

    def purge_stale_sessions(self, max_age_seconds: int = 3600):
        """Removes stale sessions from both disk and central DB registry."""
        if not os.path.exists(self.base_temp_dir):
            return
        now = time.time()
        
        # 1. Clean registry records in database
        def purge_db_tx(conn: sqlite3.Connection):
            threshold = now - max_age_seconds
            conn.execute("DELETE FROM state_registry WHERE updated_at < ?", (threshold,))
        try:
            self.state_db.run_concurrent_write(purge_db_tx)
        except Exception as e:
            logger.warning(f"[Isolation] Stale session DB purge failed: {e}")
            
        # 2. Clean directories
        for entry in os.scandir(self.base_temp_dir):
            if entry.name == "sessions_registry.db" or entry.name.startswith("sessions_registry.db-"):
                continue
            try:
                if entry.is_dir():
                    age = now - os.path.getmtime(entry.path)
                    if age > max_age_seconds:
                        shutil.rmtree(entry.path, ignore_errors=True)
                        logger.info(f"[Isolation] Removed stale session: {entry.name} (age: {int(age)}s)")
            except Exception as e:
                logger.warning(f"[Isolation] Could not inspect session dir {entry.name}: {e}")
