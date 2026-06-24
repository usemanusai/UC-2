import os
import sys
import tkinter as tk
from tkinter import messagebox
import webbrowser
import subprocess
import platform
import threading
import logging
from functools import partial

# Standardize path resolution early
try:
    import locator
except ImportError:
    # If locator fails, we are in deep trouble, but let's try to add parent to path
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent))
    import locator

try:
    from engine.kernel.model_orchestrator import ModelOrchestrator
except ImportError:
    # Fallback if engine is not yet properly in path
    ModelOrchestrator = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("application.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

def open_html_file():
    """Open the info.html file in the default browser."""
    try:
        html_path = locator.get_absolute_path("resources/info.html")
        webbrowser.open(html_path)
        logging.info("Opened info.html in the default browser.")
    except Exception as e:
        logging.error(f"Failed to open info.html: {e}")
        messagebox.showerror("Error", f"Failed to open info.html: {e}")

def check_python_version():
    """Checks if Python 3.11 or higher is installed."""
    python_version = platform.python_version()
    major, minor, micro = map(int, python_version.split('.'))
    if (major, minor) < (3, 11):
        logging.warning(f"Python 3.11 or higher is required. You have {python_version} installed.")
        message = (
            f"Python 3.11+ is required.\nYou have {python_version} installed.\n"
            "Please download Python 3.11 or higher from: https://www.python.org/downloads/"
        )
        messagebox.showerror("Python Version Mismatch", message)
        sys.exit(1)
    logging.info(f"Python version {python_version} is compatible.")

def install_or_upgrade_package(package_name):
    """Attempts to install or upgrade a package using pip."""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        logging.info(f"{package_name} package installed successfully.")
    except subprocess.CalledProcessError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", package_name])
            logging.info(f"{package_name} package upgraded successfully.")
        except subprocess.CalledProcessError:
            try:
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install", package_name, "--user", "--no-warn-script-location"
                ])
                logging.info(f"{package_name} package installed for current user.")
            except subprocess.CalledProcessError:
                logging.error(f"Failed to install {package_name}.")

def check_package_installed_upgraded(package_name):
    """Checks if a package is installed and up-to-date."""
    try:
        subprocess.check_output([sys.executable, "-m", "pip", "show", package_name])
    except subprocess.CalledProcessError:
        logging.info(f"{package_name} not found. Attempting to install.")
        install_or_upgrade_package(package_name)

def run_script(script_name):
    """Run a script asynchronously."""
    try:
        # Ensure os is available in this scope
        import os as _os
        script_path = locator.get_absolute_path(script_name)
        if not _os.path.exists(script_path):
            raise FileNotFoundError(f"Selected module script not found at {script_path}")
        
        # Use subprocess with absolute path to sys.executable
        subprocess.Popen([sys.executable, script_path, "--no-warn-script-location"])
        logging.info(f"Executed script: {script_path}")
    except Exception as e:
        logging.error(f"Error executing script: {script_name}. Exception: {e}")
        messagebox.showerror("Script Error", f"Error executing script: {script_name}\nException: {e}")

def run_system_audit():
    """Crawls directories to build a System_Audit_Log.json to prevent V1 contamination."""
    try:
        import hashlib
        import json
        audit_log = {}
        for root_dir, dirs, files in os.walk(locator.get_absolute_path(".")):
            for file in files:
                if file.endswith('.pyc') or "legacy" in file.lower() or "log" in file.lower():
                    # Identify potentially contaminating files
                    pass
                try:
                    fpath = os.path.join(root_dir, file)
                    with open(fpath, "rb") as f:
                        fhash = hashlib.md5(f.read()).hexdigest()
                    audit_log[fpath] = fhash
                except:
                    pass
        with open("System_Audit_Log.json", "w") as f:
            json.dump(audit_log, f, indent=4)
        logging.info("System_Audit_Log.json successfully generated.")
    except Exception as e:
        logging.error(f"Failed to run System Audit: {e}")

def launch_validator_v2(root):
    """Logic to terminate current main menu and hand off process to V2."""
    try:
        script_path = locator.get_absolute_path("validator_pro_v2.py")
        subprocess.Popen(
            [sys.executable, script_path, "--no-warn-script-location"]
        )
        logging.info(f"Handoff to {script_path} successful. Terminating main menu.")
        root.destroy()
        sys.exit(0)
    except Exception as e:
        logging.error(f"Failed to launch V2: {e}")
        messagebox.showerror("Error", f"Failed to launch Validator Pro V2: {e}")

def create_button(root, text, script_name, row, column, custom_command=None):
    """Create a styled button to run a script."""
    cmd = custom_command if custom_command else partial(run_script, script_name)
    button = tk.Button(
        root,
        text=text,
        command=cmd,
        bg="#1e1e1e",
        fg="#ffffff",
        font=("Inter", 10, "bold"),
        relief=tk.FLAT,
        borderwidth=0,
        padx=15,
        pady=10,
        activebackground="#00adb5",
        activeforeground="#121212"
    )
    button.grid(row=row, column=column, padx=5, pady=5, sticky="ew")
    
    # Apply specific color for V2 button
    if text == "VALIDATOR PRO V2":
        button.configure(bg="#8B5CF6", activebackground="#7C3AED")

def check_requirements():
    """Check Python version and required packages asynchronously."""
    check_python_version()
    packages = [
        "selenium", "webdriver-manager", "requests", 
        "undetected-chromedriver", "colorama", "pyautogui", "Pillow",
        "chromedriver-autoinstaller", "nest-asyncio", "httpx"
    ]

    def check_all():
        for package in packages:
            check_package_installed_upgraded(package)
        logging.info("All required packages checked.")

    thread = threading.Thread(target=check_all, daemon=True)
    thread.start()

def main_gui():
    """Main GUI Function."""
    root = tk.Tk()
    root.title(" | Portfolio")
    root.geometry("1000x500")
    root.configure(bg="#121212")

    # Configure grid
    for i in range(9):
        root.grid_rowconfigure(i, weight=1)
    for j in range(2):
        root.grid_columnconfigure(j, weight=1)

    # Info & Instructions Button
    info_button = tk.Button(
        root,
        text="INFO & INSTRUCTIONS",
        command=open_html_file,
        bg="#00adb5",
        fg="#121212",
        font=("Inter", 12, "bold"),
        relief=tk.FLAT,
        borderwidth=0,
        padx=15,
        pady=10,
        activebackground="#008a91"
    )
    info_button.grid(row=7, column=0, columnspan=2, padx=10, pady=20, sticky="ew")

    # Script Buttons
    scripts = [
        ("LEGACY MODULE", "engine/registry/legacy_settings.py", 1, 0),
        ("PROCESSOR", "engine/kernel/processor.py", 2, 0),
        ("MODERN MODULE", "engine/registry/modern_settings.py", 3, 0),
        ("PROCESSOR V1", "engine/kernel/processor_v1.py", 4, 0),
        ("PROCESSOR V2", "engine/kernel/processor_v2.py", 5, 0),
        ("VALIDATOR PRO V1", "validator_pro.py", 6, 0),
        ("VALIDATOR PRO V2", "validator_pro_v2.py", 6, 1),
        ("CLEANER", "engine/kernel/cleaner.py", 1, 1),
        ("TOOLKIT", "engine/kernel/toolkit.py", 2, 1),
        ("WEB UPDATER", "engine/utils/web_updater.py", 3, 1),
        ("DRIVER UPDATER", "engine/utils/driver_updater.py", 4, 1),
        ("PROFILE EDITOR", "engine/utils/profile_editor.py", 5, 1),
        ("HETZNER DB FETCHER", "engine/remote_db_client.py", 8, 0)
    ]

    for text, script, row, column in scripts:
        if text == "VALIDATOR PRO V2":
            create_button(root, text, script, row, column, custom_command=partial(launch_validator_v2, root))
        else:
            create_button(root, text, script, row, column)

    # Sync models if orchestrator is loaded
    if ModelOrchestrator:
        sync_thread = threading.Thread(target=ModelOrchestrator.sync_models, daemon=True)
        sync_thread.start()
    
    root.mainloop()

def main():
    """Main entry point of the application."""
    try:
        check_requirements()
        main_gui()
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}")
        messagebox.showerror("Critical Error", f"An unexpected error occurred: {sys.exc_info()[1]}")
        sys.exit(1)

if __name__ == "__main__":
    main()
