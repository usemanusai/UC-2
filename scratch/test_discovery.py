import os
import sys
import json
import subprocess

def run_agent_browser(args):
    """Safely execute npx agent-browser commands inside a subprocess on Windows 11."""
    if args and args[0] in ["npx", "agent-browser"]:
        full_args = ["cmd", "/c"] + args
    else:
        full_args = ["cmd", "/c", "npx", "agent-browser"] + args
        
    result = subprocess.run(
        full_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    return result.stdout, result.stderr, result.returncode

def test_discovery():
    target_url = "https://my.digiseller.com/inside/ad.asp"
    
    # Pre-emptively close any running background browser daemons
    print("Cleaning up background browser daemons...")
    run_agent_browser(["close", "--all"])
    
    # Scan extensions from unpacked_root
    ext_args = []
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    unpacked_root = os.path.join(proj_root, "_ext_unpacked")
    if os.path.isdir(unpacked_root):
        for item in os.listdir(unpacked_root):
            item_path = os.path.join(unpacked_root, item)
            if os.path.isdir(item_path) and os.path.isfile(os.path.join(item_path, "manifest.json")):
                ext_args.extend(["--extension", item_path])
                
    global_opts = ["--headed"]
    if ext_args:
        global_opts.extend(ext_args)
    global_opts.extend(["--args", "--no-sandbox,--disable-gpu,--disable-dev-shm-usage"])
    
    print("Global Options:", global_opts)
    
    # 1. Open and snapshot
    print("Opening page and taking snapshot...")
    cmd_chain = [["open", target_url], ["snapshot", "-i"]]
    
    full_chain_args = []
    for idx, c in enumerate(cmd_chain):
        if idx > 0:
            full_chain_args.extend(["&&", "npx", "agent-browser"])
        elif idx == 0:
            full_chain_args.extend(global_opts)
        full_chain_args.extend(c)
        
    stdout, stderr, code = run_agent_browser(full_chain_args)
    print("Snapshot return code:", code)
    print("Snapshot Stdout snippet:\n", stdout[:800])
    if stderr:
        print("Snapshot Stderr:\n", stderr)
        
    # 2. Open and eval DOM
    print("Opening page and evaluating DOM...")
    js_query = "Array.from(document.querySelectorAll('input, button, select, textarea, form, [role=\"button\"]')).map(el => ({ tag: el.tagName, id: el.id, class: el.className, name: el.name, placeholder: el.placeholder, type: el.type, text: el.innerText || el.value })).slice(0, 100)"
    
    eval_chain = [["open", target_url], ["eval", js_query]]
    eval_chain_args = []
    for idx, c in enumerate(eval_chain):
        if idx > 0:
            eval_chain_args.extend(["&&", "npx", "agent-browser"])
        elif idx == 0:
            eval_chain_args.extend(global_opts)
        eval_chain_args.extend(c)
        
    eval_stdout, eval_stderr, eval_code = run_agent_browser(eval_chain_args)
    print("Eval return code:", eval_code)
    print("Eval Stdout snippet:\n", eval_stdout[:800])
    if eval_stderr:
        print("Eval Stderr:\n", eval_stderr)

    dom_elements = []
    for line in eval_stdout.splitlines():
        try:
            parsed = json.loads(line.strip())
            if isinstance(parsed, list):
                dom_elements = parsed
                break
        except Exception:
            pass
            
    print(f"Found {len(dom_elements)} interactive DOM elements in parsed list.")
    if dom_elements:
        print("First 3 elements:", dom_elements[:3])

if __name__ == "__main__":
    test_discovery()
