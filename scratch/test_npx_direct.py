import subprocess

# Test running npx.cmd directly without cmd /c
js_query = "Array.from(document.querySelectorAll('input')).map(el => el.tagName)"
full_args = ["npx.cmd", "agent-browser", "eval", js_query]

print("Executing:", full_args)
result = subprocess.run(
    full_args,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace"
)
print("Return code:", result.returncode)
print("Stdout:", result.stdout)
print("Stderr:", result.stderr)
