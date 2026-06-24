import subprocess

def test_run():
    # Test if agent-browser accepts standard CSS selectors in fill
    args1 = [
        "pwsh", "-NoProfile", "-Command",
        "npx -y agent-browser --json batch 'open https://my.digiseller.com/inside/ad.asp' 'fill #login \"testuser_by_selector\"' 'snapshot -i'"
    ]
    print("Running with CSS selector #login...")
    res1 = subprocess.run(args1, capture_output=True, text=True, encoding="utf-8")
    with open("scratch/out1.txt", "w", encoding="utf-8") as f:
        f.write(res1.stdout + "\n" + res1.stderr)

    print("Done! Check out1.txt.")

if __name__ == "__main__":
    test_run()
