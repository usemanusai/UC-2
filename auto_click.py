"""
GUI automation v3: uses keyboard Home key to guarantee Profile 1 is selected.
Falls back to clicking Check Accounts directly if profile selector not found.
"""
import time, sys, subprocess

try:
    import pyautogui
    import pygetwindow as gw
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pyautogui", "pygetwindow", "-q"])
    import pyautogui
    import pygetwindow as gw

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.3

def select_profile_1_via_keyboard():
    """
    Activates the profile selector window and uses keyboard navigation
    to guarantee Profile 1 is always selected — regardless of window position.
    Returns True if profile was selected, False if window was not found.
    """
    print("[Auto] Waiting for profile selector window...")
    for attempt in range(120):  # Wait up to 60s (120 x 0.5s) for GUI to appear
        wins = gw.getWindowsWithTitle("Select Chrome Profile")
        if wins:
            w = wins[0]
            try:
                w.activate()
            except Exception:
                pass
            time.sleep(0.7)
            print(f"[Auto] Found profile selector: ({w.left},{w.top}) {w.width}x{w.height}")

            # Click the center of the listbox area to give it focus
            list_x = w.left + w.width // 2
            list_y = w.top + 85
            pyautogui.click(list_x, list_y)
            time.sleep(0.4)

            # Press Home to jump to the very first item (Profile 1)
            pyautogui.press('home')
            time.sleep(0.3)
            print("[Auto] Pressed Home — Profile 1 should be selected")

            # Click Select Profile button at bottom of dialog
            btn_x = w.left + w.width // 2
            btn_y = w.top + w.height - 35
            pyautogui.click(btn_x, btn_y)
            print(f"[Auto] Clicked 'Select Profile' at ({btn_x}, {btn_y})")
            return True

        time.sleep(0.5)

    print("[Auto] Profile selector not found — profile may already be loaded. Skipping.")
    return False


def click_check_accounts():
    """Wait for main window to load, then click Check Accounts."""
    print("[Auto] Waiting 5s for profile to fully load...")
    time.sleep(5)

    for attempt in range(60):  # poll up to 30s for main window
        for title_frag in ["Universal Checker", "Checker", "Validator"]:
            wins = [w for w in gw.getAllWindows()
                    if title_frag.lower() in w.title.lower()
                    and w.width > 600
                    and "Select Chrome" not in w.title]
            if wins:
                w = wins[0]
                try:
                    w.activate()
                except Exception:
                    pass
                time.sleep(0.5)
                print(f"[Auto] Main window: '{w.title}' ({w.left},{w.top}) {w.width}x{w.height}")

                # Check Accounts button is in the bottom-right corner
                btn_x = w.left + w.width - 70
                btn_y = w.top + w.height - 35
                pyautogui.click(btn_x, btn_y)
                print(f"[Auto] Clicked 'Check Accounts' at ({btn_x},{btn_y})")
                return True
        time.sleep(0.5)

    print("[Auto] ERROR: Main window not found after 10s")
    return False


if __name__ == "__main__":
    time.sleep(5)  # Give validator time to complete pre-warming (~15s)
    select_profile_1_via_keyboard()  # Attempt profile selection (no-op if already loaded)
    click_check_accounts()           # Always attempt to click Check Accounts
    print("[Auto] Done.")
