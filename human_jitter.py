# human_jitter.py
import random
import time
import math
import logging
import re
from typing import List, Tuple, Dict
import numpy as np

# Import our new fLE/fBm mathematical motor control engine
from engine.kernel.math_engine.langevin import FLEMotorControl, generate_fbm
from selenium.webdriver.common.keys import Keys

logger = logging.getLogger(__name__)

class HumanJitter:
    """
    Implements advanced behavioral mimicry using chaotic fLE trajectories,
    Fractional Brownian Motion noise, and Bubble Entropy validation.
    Adapts to Linguistics Chameleon AI Personas dynamically.
    """
    current_persona = {
        "type": "systematic_researcher",
        "wpm": 60,
        "hesitation": 0.05,
        "curve_offset": 50,
        "scroll_aggressiveness": 0.3
    }
    
    # Mapping table to translate shifted/complex keys into base key + shift key sequences
    SHIFT_MAP = {
        '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
        '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
        '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\',
        ':': ';', '"': "'", '<': ',', '>': '.', '?': '/'
    }
    
    @classmethod
    def set_persona(cls, persona_data: Dict):
        """Applies a new AI-generated persona to the session's behavioral signature."""
        cls.current_persona.update(persona_data)
        logger.info(f"[Jitter] Persona updated: {cls.current_persona['type']}")
  
    @staticmethod
    def bezier_curve(p0: Tuple[int, int], p1: Tuple[int, int], p2: Tuple[int, int], p3: Tuple[int, int], steps: int = 20) -> List[Tuple[int, int]]:
        """Legacy cubic Bézier curve generator for compatibility."""
        return HumanJitter.bezier_curve_with_easing(p0, p1, p2, p3, steps, easing_fn=lambda u: u)
  
    @staticmethod
    def bezier_curve_with_easing(p0: Tuple[float, float], p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float], steps: int = 20, easing_fn=None) -> List[Tuple[int, int]]:
        """
        Generates a cubic Bézier curve path using an easing function.
        Used as a fallback or for legacy components.
        """
        if easing_fn is None:
            easing_fn = lambda u: math.sin(u * math.pi / 2)
            
        path = []
        for i in range(steps + 1):
            u = i / steps
            t = easing_fn(u)
            x = (1-t)**3 * p0[0] + 3*(1-t)**2 * t * p1[0] + 3*(1-t) * t**2 * p2[0] + t**3 * p3[0]
            y = (1-t)**3 * p0[1] + 3*(1-t)**2 * t * p1[1] + 3*(1-t) * t**2 * p2[1] + t**3 * p3[1]
            path.append((int(x), int(y)))
        return path

    @staticmethod
    def move_mouse_stealth(target_x: int, target_y: int, element=None, element_width: int = None, element_height: int = None, is_click: bool = True):
        """
        Moves the mouse to target coordinates by delegating path generation to the
        Fractional Langevin Equation (fLE) motor control engine.
        Includes a 5% chance of misclicking outside the target bounds and correcting back.
        """
        import pyautogui
        start_x, start_y = pyautogui.position()
        
        # Determine Hurst range from persona
        offset = HumanJitter.current_persona.get("curve_offset", 100)
        h_min = max(0.35, 0.6 - (offset / 300.0))
        h_max = min(0.85, 0.9 - (offset / 500.0))
        
        # Instantiate fLE Motor Control
        motor = FLEMotorControl(H_range=(h_min, h_max))
        
        # 5% chance of misclick outside active element bounds
        if is_click and random.random() < 0.05:
            # Determine active bounds of the element
            width = 100
            height = 30
            if element_width is not None:
                width = element_width
            if element_height is not None:
                height = element_height
            elif element is not None:
                try:
                    width = element.size.get('width', 100)
                    height = element.size.get('height', 30)
                except Exception:
                    pass
            
            # Compute a coordinate outside the bounding box
            offset_x = random.randint(5, 25)
            offset_y = random.randint(5, 25)
            dir_x = random.choice([-1, 1])
            dir_y = random.choice([-1, 1])
            
            misclick_x = target_x + dir_x * (width // 2 + offset_x)
            misclick_y = target_y + dir_y * (height // 2 + offset_y)
            
            # Clamp to viewport bounds
            misclick_x = max(0, min(1920, misclick_x))
            misclick_y = max(0, min(1080, misclick_y))
            
            # 1. Path to misclick target
            dist = math.hypot(misclick_x - start_x, misclick_y - start_y)
            if dist >= 5:
                steps = int(max(15, min(dist / 6.0, 100)))
                path1 = motor.generate_path(
                    start=(start_x, start_y),
                    target=(misclick_x, misclick_y),
                    steps=steps,
                    dt=0.005,
                    mass=1.1,
                    eta=0.35,
                    k_p=2.2,
                    k_d=0.3
                )
                for x, y in path1:
                    pyautogui.moveTo(int(x), int(y))
                    curr_dist = math.hypot(misclick_x - x, misclick_y - y)
                    if curr_dist < 50:
                        time.sleep(random.uniform(0.015, 0.045))
                    else:
                        time.sleep(random.uniform(0.005, 0.015))
            else:
                pyautogui.moveTo(misclick_x, misclick_y)
                
            # 2. Click outside bounds (misclick)
            pyautogui.click()
            logger.info(f"[Jitter] Performed misclick at ({misclick_x}, {misclick_y}) instead of ({target_x}, {target_y})")
            
            # 3. Pause (reaction delay)
            time.sleep(random.uniform(0.15, 0.40))
            
            # 4. Path back to target (correction trajectory)
            correct_dist = math.hypot(target_x - misclick_x, target_y - misclick_y)
            if correct_dist >= 5:
                correct_steps = int(max(15, min(correct_dist / 6.0, 100)))
                path2 = motor.generate_path(
                    start=(misclick_x, misclick_y),
                    target=(target_x, target_y),
                    steps=correct_steps,
                    dt=0.005,
                    mass=1.1,
                    eta=0.35,
                    k_p=2.2,
                    k_d=0.3
                )
                for x, y in path2:
                    pyautogui.moveTo(int(x), int(y))
                    curr_dist = math.hypot(target_x - x, target_y - y)
                    if curr_dist < 50:
                        time.sleep(random.uniform(0.015, 0.045))
                    else:
                        time.sleep(random.uniform(0.005, 0.015))
            else:
                pyautogui.moveTo(target_x, target_y)
                
            logger.info(f"[Jitter] Corrected cursor trajectory back to target ({target_x}, {target_y})")
            return

        # Normal movement without misclick
        dx = target_x - start_x
        dy = target_y - start_y
        dist = math.sqrt(dx**2 + dy**2)
        
        if dist < 5:
            pyautogui.moveTo(target_x, target_y)
            return

        # Determine step count based on distance and velocity profile
        steps = int(max(15, min(dist / 6.0, 100)))
        
        # Generate the fLE simulated trajectory
        path = motor.generate_path(
            start=(start_x, start_y),
            target=(target_x, target_y),
            steps=steps,
            dt=0.005,
            mass=1.1,
            eta=0.35,
            k_p=2.2,
            k_d=0.3
        )
        
        # Move the mouse along the generated path
        for x, y in path:
            pyautogui.moveTo(int(x), int(y))
            curr_dist = math.hypot(target_x - x, target_y - y)
            if curr_dist < 50:
                # Decelerate near target (Fitts's Law)
                time.sleep(random.uniform(0.015, 0.045))
            else:
                time.sleep(random.uniform(0.005, 0.015))
                
        logger.info(f"[Jitter] Moved mouse (fLE / H={h_max:.2f} style) to ({target_x}, {target_y}).")

    @staticmethod
    def _generate_typing_operations(text: str) -> List[Dict[str, Any]]:
        """
        Parses text into a list of keystroke operations, introducing
        probabilistic typos (4% chance per word) corrected by backspaces.
        """
        parts = re.split(r'(\w+)', text)
        operations = []
        for part in parts:
            if not part:
                continue
            if part.isalnum() and len(part) >= 2:
                # 4% chance of typo per word
                if random.random() < 0.04:
                    typo_idx = random.randint(0, len(part) - 1)
                    correct_char = part[typo_idx]
                    
                    # Keyboard adjacency map
                    qwerty_adj = {
                        'a': 'qwsz', 'b': 'vghn', 'c': 'xdfv', 'd': 'ersfxc', 'e': 'wsdr',
                        'f': 'rtgvcd', 'g': 'tyhbvf', 'h': 'yujnbg', 'i': 'ujko', 'j': 'uikmnh',
                        'k': 'ijlm', 'l': 'okp', 'm': 'njk', 'n': 'bhjm', 'o': 'iklp',
                        'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'wedxza', 't': 'rfgy',
                        'u': 'yhji', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu',
                        'z': 'asx',
                        'A': 'QWSZ', 'B': 'VGHN', 'C': 'XDFV', 'D': 'ERSFXC', 'E': 'WSDR',
                        'F': 'RTGVCD', 'G': 'TYHBVF', 'H': 'YUJNBG', 'I': 'UJKO', 'J': 'UIKMNH',
                        'K': 'IJLM', 'L': 'OKP', 'M': 'NJK', 'N': 'BHJM', 'O': 'IKLP',
                        'P': 'OL', 'Q': 'WA', 'R': 'EDFT', 'S': 'WEDXZA', 'T': 'RFGY',
                        'U': 'YHJI', 'V': 'CFGB', 'W': 'QASE', 'X': 'ZSDC', 'Y': 'TGHU',
                        'Z': 'ASX',
                        '1': '2q', '2': '13qw', '3': '24we', '4': '35er', '5': '46rt',
                        '6': '57ty', '7': '68yu', '8': '79io', '9': '80op', '0': '9-p'
                    }
                    candidates = qwerty_adj.get(correct_char, "qwertyuiopasdfghjklzxcvbnm")
                    typo_char = random.choice(candidates)
                    
                    if correct_char.isupper() and typo_char.islower():
                        typo_char = typo_char.upper()
                    elif correct_char.islower() and typo_char.isupper():
                        typo_char = typo_char.lower()
                    
                    for c in part[:typo_idx]:
                        operations.append({"op": "type", "char": c})
                    operations.append({"op": "type", "char": typo_char})
                    operations.append({"op": "wait", "duration": random.uniform(0.15, 0.30)})
                    operations.append({"op": "backspace"})
                    operations.append({"op": "wait", "duration": random.uniform(0.05, 0.15)})
                    operations.append({"op": "type", "char": correct_char})
                    for c in part[typo_idx+1:]:
                        operations.append({"op": "type", "char": c})
                else:
                    for c in part:
                        operations.append({"op": "type", "char": c})
            else:
                for c in part:
                    operations.append({"op": "type", "char": c})
        return operations

    @staticmethod
    def human_typing(text: str, element=None):
        """
        Types text with realistic inter-character delays simulated using
        Fractional Brownian Motion (fBm) noise to mimic human typing cadence,
        incorporating probabilistic typos corrected by backspaces.
        """
        base_wpm = HumanJitter.current_persona.get("wpm", 50)
        wpm = random.randint(base_wpm - 8, base_wpm + 8)
        chars_per_sec = (wpm * 5) / 60
        base_delay = 1.0 / chars_per_sec
        hesitation = HumanJitter.current_persona.get("hesitation", 0.08)
        
        is_headless = False
        if element:
            try:
                element.click()
            except Exception:
                pass
            try:
                driver = element.parent
                is_headless = driver.capabilities.get('headless', False) or 'headless' in str(driver.capabilities)
            except Exception:
                pass

        # Generate the sequence of keystroke operations (with typos)
        operations = HumanJitter._generate_typing_operations(text)

        # Generate Fractional Brownian Motion (fBm) noise for typing delay fluctuations
        total_ops = len(operations)
        if total_ops > 1:
            fbm = generate_fbm(total_ops, H=0.60, sigma=0.8)
            fgn = np.diff(fbm)
            fgn = np.append(fgn, fgn[-1])
        else:
            fgn = np.zeros(1)

        # Headless mode fallback typing
        if is_headless and element:
            for i, op in enumerate(operations):
                op_type = op["op"]
                delay = base_delay * (1.0 + 0.4 * fgn[i])
                delay = max(delay, 0.01)
                
                if op_type == "type":
                    char = op["char"]
                    if char.isupper() or char in "!@#$%^&*()_+{}|:\"<>?":
                        delay += random.uniform(0.08, 0.15)
                    if random.random() < hesitation:
                        time.sleep(random.uniform(0.15, 0.4))
                    element.send_keys(char)
                    time.sleep(delay)
                elif op_type == "backspace":
                    element.send_keys(Keys.BACKSPACE)
                    time.sleep(delay)
                elif op_type == "wait":
                    time.sleep(op["duration"])
            logger.info(f"[Jitter] Completed headless typing session (WPM: {wpm})")
            return

        # Native headed mode: type with press/release overlap and asynchronous timing profiles
        import pyautogui
        events = []
        current_time = 0.0
        
        for i, op in enumerate(operations):
            op_type = op["op"]
            if op_type == "wait":
                current_time += op["duration"]
                continue
                
            char = 'backspace' if op_type == "backspace" else op["char"]
            mapped_char = char
            if char == '\n':
                mapped_char = 'enter'
            elif char == '\t':
                mapped_char = 'tab'
                
            # Apply fBm noise scale to typing delays
            char_delay = base_delay * (1.0 + 0.4 * fgn[i])
            char_delay = max(char_delay, 0.02)
            
            if char.isupper() or char in "!@#$%^&*()_+{}|:\"<>?":
                char_delay += random.uniform(0.08, 0.15)
                
            if random.random() < hesitation:
                char_delay += random.uniform(0.15, 0.4)
                
            press_time = current_time + char_delay
            hold_duration = random.uniform(0.04, 0.12)
            release_time = press_time + hold_duration
            
            # Map key overlaps using virtual events
            if mapped_char == 'backspace':
                events.append((press_time, 'down', 'backspace'))
                events.append((release_time, 'up', 'backspace'))
            elif mapped_char.islower() or mapped_char.isdigit() or mapped_char in " \t\n-= []\\;',./" or mapped_char in ['enter', 'tab']:
                events.append((press_time, 'down', mapped_char))
                events.append((release_time, 'up', mapped_char))
            elif mapped_char.isupper():
                lower = mapped_char.lower()
                events.append((press_time - 0.015, 'down', 'shift'))
                events.append((press_time, 'down', lower))
                events.append((release_time, 'up', lower))
                events.append((release_time + 0.015, 'up', 'shift'))
            elif mapped_char in HumanJitter.SHIFT_MAP:
                base = HumanJitter.SHIFT_MAP[mapped_char]
                events.append((press_time - 0.015, 'down', 'shift'))
                events.append((press_time, 'down', base))
                events.append((release_time, 'up', base))
                events.append((release_time + 0.015, 'up', 'shift'))
            else:
                events.append((press_time, 'press', mapped_char))
                
            # Random overlap schedule for natural typing cadence
            if i < total_ops - 1:
                if random.random() < 0.38: # 38% chance of key overlap
                    overlap_duration = random.uniform(0.01, 0.04)
                    current_time = release_time - overlap_duration
                else:
                    current_time = release_time + random.uniform(0.015, 0.06)
                    
        # Sort and run the scheduled events sequentially
        events.sort(key=lambda x: x[0])
        
        last_time = 0.0
        for event_time, action, key in events:
            sleep_time = event_time - last_time
            if sleep_time > 0:
                time.sleep(sleep_time)
            last_time = event_time
            
            try:
                if action == 'down':
                    pyautogui.keyDown(key)
                elif action == 'up':
                    pyautogui.keyUp(key)
                else:
                    pyautogui.write(key)
            except Exception:
                try:
                    pyautogui.write(key)
                except Exception:
                    pass
                    
        logger.info(f"[Jitter] Completed physical/headed typing session (WPM: {wpm})")

    @staticmethod
    def random_scroll(browser):
        """
        Performs non-linear scrolling based on scroll aggressiveness and fBm noise.
        """
        agg = HumanJitter.current_persona.get("scroll_aggressiveness", 0.5)
        scroll_amount = random.randint(250, int(300 * (1.0 + agg * 2)))
        direction = 1 if random.random() > 0.25 else -1
        
        steps = random.randint(5, 12)
        # Generate fBm path to modulate scroll steps to simulate finger drag decay
        fbm = generate_fbm(steps, H=0.55, sigma=1.0)
        fgn = np.abs(np.diff(fbm))
        fgn = np.append(fgn, fgn[-1])
        # Normalize fgn to sum to 1.0
        if np.sum(fgn) > 0:
            fgn = fgn / np.sum(fgn)
        else:
            fgn = np.ones(steps) / steps
            
        for i in range(steps):
            step_scroll = int(scroll_amount * fgn[i] * direction)
            browser.execute_script(f"window.scrollBy(0, {step_scroll});")
            time.sleep(random.uniform(0.05, 0.2) + 0.1 * (1.0 - agg))
            
        logger.info(f"[Jitter] Performed fBm scroll: {scroll_amount}px in {steps} steps")
