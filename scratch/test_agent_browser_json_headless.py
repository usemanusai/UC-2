import subprocess
import json

def test_run():
    # Use a realistic JS query with spaces, parentheses, double quotes, etc.
    # We will single-quote each command inside the powershell invocation.
    js_query = "eval (function(){ return 1 + 2; })()"
    
    batch_commands = [
        "open https://my.digiseller.com/inside/ad.asp",
        "snapshot -i",
        js_query
    ]
    
    # Format each command with single quotes. To handle potential single quotes
    # in the JS itself, we can double them or escape them, but for this query
    # simple single quotes are perfect.
    formatted_cmds = " ".join(f"'{cmd}'" for cmd in batch_commands)
    
    pwsh_cmd = f"npx -y agent-browser --json batch {formatted_cmds}"
    args = ["pwsh", "-NoProfile", "-Command", pwsh_cmd]
    
    print("Running:", " ".join(args))
    
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    
    print("Return Code:", result.returncode)
    print("STDOUT:")
    print(result.stdout)
    print("STDERR:")
    print(result.stderr)

if __name__ == "__main__":
    test_run()
