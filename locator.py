import os
import sys

def get_project_root():
    """Returns the absolute path to the project root directory."""
    return os.path.abspath(os.path.dirname(__file__))

def get_absolute_path(*path_parts):
    """Returns an absolute path joined with the project root."""
    return os.path.join(get_project_root(), *path_parts)

# Automatically add engine to sys.path
root = get_project_root()
if root not in sys.path:
    sys.path.append(root)

def get_chrome_user_data_dir():
    """Returns a dynamic path to Chrome User Data."""
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")

def get_chrome_exe_path():
    """Returns standard Chrome executable path."""
    paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return "chrome.exe" # Fallback to path

if __name__ == "__main__":
    print(f"Project Root: {get_project_root()}")
    print(f"Chrome User Data: {get_chrome_user_data_dir()}")
    print(f"Chrome Exe: {get_chrome_exe_path()}")
