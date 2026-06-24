import subprocess
import json

def test_run():
    args = [
        "npx", "agent-browser",
        "--headed",
        "--json",
        "batch",
        "open https://my.digiseller.com/inside/ad.asp",
        "snapshot -i",
        "eval 1+1"
    ]
    
    full_args = ["cmd", "/c"] + args
    print("Running:", " ".join(full_args))
    
    result = subprocess.run(
        full_args,
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
