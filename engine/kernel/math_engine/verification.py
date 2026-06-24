# engine/kernel/math_engine/verification.py
"""
Neuro-Symbolic Formal Verification Bridge.
Uses the Z3 SMT solver to verify logical and semantic constraints on actions,
implementing a verifier-in-the-loop feedback mechanism for self-correction.
"""

import z3
import numpy as np
from typing import List, Dict, Any, Tuple, Optional

class Z3ActionVerifier:
    """
    Formulates UI automation action sequences as a Constraint Satisfaction Problem (CSP)
    and verifies them using the Z3 SMT solver.
    """
    def __init__(self, viewport_width: int = 1920, viewport_height: int = 1080):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height

    def verify_sequence(self, actions: List[Dict[str, Any]]) -> Tuple[bool, str, Optional[List[str]]]:
        """
        Translates a list of actions into SMT constraints and checks satisfiability.
        Returns:
            Tuple of (is_valid, message, counterexamples_or_cores)
        """
        solver = z3.Solver()
        
        # Define limits
        max_text_len = 256
        max_total_text_len = 1024
        
        # Navigation indices tracking
        email_idx = -1
        next_btn_idx = -1
        password_idx = -1
        submit_idx = -1
        total_text_len_val = 0

        # Action types: 0=click, 1=type, 2=scroll, 3=wait, 4=submit
        # We define SMT variables for each action step
        for i, act in enumerate(actions):
            act_type_str = act.get("type", "wait").lower()
            
            # Translate action type to integer code
            type_code = 3 # Default wait
            if act_type_str == "click":
                type_code = 0
            elif act_type_str == "type":
                type_code = 1
            elif act_type_str == "scroll":
                type_code = 2
            elif act_type_str == "submit":
                type_code = 4

            # Variables for this step
            x = z3.Int(f"act_{i}_x")
            y = z3.Int(f"act_{i}_y")
            w = z3.Int(f"act_{i}_w")
            h = z3.Int(f"act_{i}_h")
            t = z3.Int(f"act_{i}_time")
            ac_type = z3.Int(f"act_{i}_type")
            
            # 1. Coordinate boundaries (must fit in viewport if interacting)
            solver.add(ac_type == type_code)
            
            # If interaction involves coordinates (click, type, scroll)
            if type_code in [0, 1, 2]:
                elem_x = act.get("x", -1)
                elem_y = act.get("y", -1)
                elem_w = act.get("width", 0)
                elem_h = act.get("height", 0)
                
                # Assert coordinates match actual DOM element bounds
                solver.add(x == elem_x)
                solver.add(y == elem_y)
                solver.add(w == elem_w)
                solver.add(h == elem_h)
                
                # Check bounds constraint
                solver.add(x >= 0)
                solver.add(y >= 0)
                solver.add(x + w <= self.viewport_width)
                solver.add(y + h <= self.viewport_height)
                solver.add(w >= 0)
                solver.add(h >= 0)
            else:
                # wait/submit might not have coordinates
                solver.add(x == -1)
                solver.add(y == -1)
                solver.add(w == 0)
                solver.add(h == 0)

            # Assert time sequencing
            time_val = act.get("timestamp", 0)
            solver.add(t == int(time_val))
            solver.add(t >= 0)
            
            if i > 0:
                prev_t = z3.Int(f"act_{i-1}_time")
                prev_act = actions[i-1]
                prev_type_str = prev_act.get("type", "wait").lower()
                prev_type_code = {"click":0, "type":1, "scroll":2, "wait":3, "submit":4}.get(prev_type_str, 3)
                
                # Consecutiveness click separation: 300ms if same target, else 100ms
                if type_code == 0 and prev_type_code == 0 and act.get("x") == prev_act.get("x") and act.get("y") == prev_act.get("y"):
                    solver.add(t - prev_t >= 300)
                else:
                    solver.add(t - prev_t >= 100)

            # Specific logical rules:
            # Rule: Click targets must have a positive area
            if type_code == 0:
                solver.add(z3.Or(w > 0, h > 0))

            # Rule: Text fields cannot be typed into if width/height are zero
            if type_code == 1:
                solver.add(w > 0)
                solver.add(h > 0)
                text_val = act.get("text", "")
                text_len = len(text_val)
                solver.add(text_len > 0)
                solver.add(text_len <= max_text_len)
                total_text_len_val += text_len

            # Tag interaction rules
            tag = act.get("tag", "").upper() if act.get("tag") else ""
            if tag:
                if type_code == 1:
                    # Cannot type into elements other than inputs/textareas
                    solver.add(z3.BoolVal(tag in ["INPUT", "TEXTAREA", "SELECT", "IFRAME"]))
                if type_code in [0, 1, 2]:
                    # Cannot interact with hidden elements
                    solver.add(z3.BoolVal(tag != "HIDDEN"))

            # Classify navigation state machine indices
            field_name = act.get("field", "").lower() if act.get("field") else ""
            text_lower = act.get("text", "").lower() if act.get("text") else ""
            
            if type_code == 1: # type
                if "email" in field_name or "user" in field_name or "email" in text_lower or "user" in text_lower:
                    email_idx = i
                elif "password" in field_name or "pass" in field_name:
                    password_idx = i
            elif type_code == 0: # click
                if "next" in field_name or "next" in text_lower or "next" in tag.lower():
                    next_btn_idx = i
                elif "submit" in field_name or "login" in field_name or "submit" in text_lower or "login" in text_lower:
                    submit_idx = i
            elif type_code == 4: # submit
                submit_idx = i

        # Enforce global text limits
        solver.add(total_text_len_val <= max_total_text_len)

        # Enforce navigation order invariants via Z3 variables
        email_idx_var = z3.Int("email_idx_var")
        next_btn_idx_var = z3.Int("next_btn_idx_var")
        password_idx_var = z3.Int("password_idx_var")
        submit_idx_var = z3.Int("submit_idx_var")

        solver.add(email_idx_var == email_idx)
        solver.add(next_btn_idx_var == next_btn_idx)
        solver.add(password_idx_var == password_idx)
        solver.add(submit_idx_var == submit_idx)

        # Logical dependency transitions
        solver.add(z3.Implies(password_idx_var >= 0, z3.And(email_idx_var >= 0, email_idx_var < password_idx_var)))
        solver.add(z3.Implies(z3.And(email_idx_var >= 0, next_btn_idx_var >= 0), email_idx_var < next_btn_idx_var))
        solver.add(z3.Implies(z3.And(next_btn_idx_var >= 0, password_idx_var >= 0), next_btn_idx_var < password_idx_var))
        solver.add(z3.Implies(z3.And(password_idx_var >= 0, submit_idx_var >= 0), password_idx_var < submit_idx_var))

        # Check satisfiability of the system
        if solver.check() == z3.sat:
            return True, "Logical SAT: Action sequence formally verified.", None
        else:
            # Extract unsatisfiable core or constraints that caused conflict
            unsat_core = []
            try:
                # We can re-check with assumptions to identify which steps failed
                solver.reset()
                assumptions = []
                total_text_len_val = 0
                email_idx = -1
                next_btn_idx = -1
                password_idx = -1
                submit_idx = -1

                for i, act in enumerate(actions):
                    p = z3.Bool(f"step_{i}_ok")
                    assumptions.append(p)
                    
                    # Re-add constraints gated by step boolean
                    act_type_str = act.get("type", "wait").lower()
                    type_code = {"click":0, "type":1, "scroll":2, "wait":3, "submit":4}.get(act_type_str, 3)
                    
                    x = z3.Int(f"act_{i}_x")
                    y = z3.Int(f"act_{i}_y")
                    w = z3.Int(f"act_{i}_w")
                    h = z3.Int(f"act_{i}_h")
                    t = z3.Int(f"act_{i}_time")
                    ac_type = z3.Int(f"act_{i}_type")
                    
                    solver.add(z3.Implies(p, ac_type == type_code))
                    if type_code in [0, 1, 2]:
                        solver.add(z3.Implies(p, x == act.get("x", -1)))
                        solver.add(z3.Implies(p, y == act.get("y", -1)))
                        solver.add(z3.Implies(p, w == act.get("width", 0)))
                        solver.add(z3.Implies(p, h == act.get("height", 0)))
                        solver.add(z3.Implies(p, x >= 0))
                        solver.add(z3.Implies(p, y >= 0))
                        solver.add(z3.Implies(p, x + w <= self.viewport_width))
                        solver.add(z3.Implies(p, y + h <= self.viewport_height))
                    solver.add(z3.Implies(p, t == int(act.get("timestamp", 0))))
                    if i > 0:
                        prev_t = z3.Int(f"act_{i-1}_time")
                        prev_act = actions[i-1]
                        prev_type_str = prev_act.get("type", "wait").lower()
                        prev_type_code = {"click":0, "type":1, "scroll":2, "wait":3, "submit":4}.get(prev_type_str, 3)
                        
                        if type_code == 0 and prev_type_code == 0 and act.get("x") == prev_act.get("x") and act.get("y") == prev_act.get("y"):
                            solver.add(z3.Implies(p, t - prev_t >= 300))
                        else:
                            solver.add(z3.Implies(p, t - prev_t >= 100))

                    if type_code == 0:
                        solver.add(z3.Implies(p, z3.Or(w > 0, h > 0)))
                    elif type_code == 1:
                        solver.add(z3.Implies(p, w > 0))
                        solver.add(z3.Implies(p, h > 0))
                        text_val = act.get("text", "")
                        text_len = len(text_val)
                        solver.add(z3.Implies(p, text_len > 0))
                        solver.add(z3.Implies(p, text_len <= max_text_len))
                        total_text_len_val += text_len

                    tag = act.get("tag", "").upper() if act.get("tag") else ""
                    if tag:
                        if type_code == 1:
                            solver.add(z3.Implies(p, z3.BoolVal(tag in ["INPUT", "TEXTAREA", "SELECT", "IFRAME"])))
                        if type_code in [0, 1, 2]:
                            solver.add(z3.Implies(p, z3.BoolVal(tag != "HIDDEN")))

                    field_name = act.get("field", "").lower() if act.get("field") else ""
                    text_lower = act.get("text", "").lower() if act.get("text") else ""
                    if type_code == 1:
                        if "email" in field_name or "user" in field_name or "email" in text_lower or "user" in text_lower:
                            email_idx = i
                        elif "password" in field_name or "pass" in field_name:
                            password_idx = i
                    elif type_code == 0:
                        if "next" in field_name or "next" in text_lower or "next" in tag.lower():
                            next_btn_idx = i
                        elif "submit" in field_name or "login" in field_name or "submit" in text_lower or "login" in text_lower:
                            submit_idx = i
                    elif type_code == 4:
                        submit_idx = i

                solver.add(total_text_len_val <= max_total_text_len)

                email_idx_var = z3.Int("email_idx_var")
                next_btn_idx_var = z3.Int("next_btn_idx_var")
                password_idx_var = z3.Int("password_idx_var")
                submit_idx_var = z3.Int("submit_idx_var")

                solver.add(email_idx_var == email_idx)
                solver.add(next_btn_idx_var == next_btn_idx)
                solver.add(password_idx_var == password_idx)
                solver.add(submit_idx_var == submit_idx)

                solver.add(z3.Implies(password_idx_var >= 0, z3.And(email_idx_var >= 0, email_idx_var < password_idx_var)))
                solver.add(z3.Implies(z3.And(email_idx_var >= 0, next_btn_idx_var >= 0), email_idx_var < next_btn_idx_var))
                solver.add(z3.Implies(z3.And(next_btn_idx_var >= 0, password_idx_var >= 0), next_btn_idx_var < password_idx_var))
                solver.add(z3.Implies(z3.And(password_idx_var >= 0, submit_idx_var >= 0), password_idx_var < submit_idx_var))

                if solver.check(assumptions) == z3.unsat:
                    core = solver.unsat_core()
                    unsat_core = [str(c) for c in core]
            except Exception as e:
                unsat_core = [f"Exception during unsat core extraction: {str(e)}"]

            # Generate formal explanation message
            explanation = "Logical UNSAT: Action sequence violated formal constraints. "
            if unsat_core:
                explanation += f"Failed elements/assumptions: {', '.join(unsat_core)}."
            else:
                explanation += "Viewport boundaries, sequential timing, text limits, tag interaction, or navigation order constraints violated."
                
            return False, explanation, unsat_core

def verify_semantic_similarity(action_desc: str, target_elem_desc: str, threshold: float = 0.45) -> bool:
    """
    Verifies semantic alignment between the action description and the element description
    using a fast Jaccard/Cosine token overlap metric (acting as local SMT semantic constraint).
    """
    def tokenize(s: str) -> set:
        return set(s.lower().replace("_", " ").replace("-", " ").split())
        
    t1 = tokenize(action_desc)
    t2 = tokenize(target_elem_desc)
    
    if not t1 or not t2:
        return False
        
    intersection = t1.intersection(t2)
    union = t1.union(t2)
    similarity = len(intersection) / len(union)
    
    return similarity >= threshold

class VerifierInTheLoop:
    """
    Orchestrates the verifier-in-the-loop mechanism: captures LLM action sequences,
    validates them via SMT/Z3, and feeds back logical counterexamples for correction.
    """
    def __init__(self, verifier: Z3ActionVerifier):
        self.verifier = verifier

    def run_verification_loop(
        self, 
        action_generator_fn, 
        prompt: str, 
        max_attempts: int = 4
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        Executes the verifier loop. If action_generator_fn produces an UNSAT sequence,
        feeds the core reason back to the generator.
        """
        current_prompt = prompt
        attempts_log = []
        
        for attempt in range(max_attempts):
            # Generate the action sequence from the LLM or proxy generator
            actions = action_generator_fn(current_prompt)
            
            # Check constraints via SMT solver
            is_valid, msg, unsat_core = self.verifier.verify_sequence(actions)
            attempts_log.append({
                "attempt": attempt + 1,
                "actions": actions,
                "is_valid": is_valid,
                "message": msg
            })
            
            if is_valid:
                return True, actions, f"SAT on attempt {attempt+1}."
                
            # If UNSAT, append the SMT counterexample back to the prompt
            feedback = (
                f"\n[VERIFICATION FAILURE] Attempt {attempt+1} generated code/actions failed SMT verification: {msg}\n"
                "Please correct the actions sequence to respect coordinate bounds (x,y >= 0, x+w <= 1920, y+h <= 1080), "
                "ensure strict positive widths/heights for element interaction, and maintain time spacing >= 100ms."
            )
            current_prompt += feedback
            
        return False, [], f"UNSAT after {max_attempts} attempts. Log: {attempts_log}"
