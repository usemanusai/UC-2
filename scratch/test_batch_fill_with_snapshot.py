import subprocess

def test_run():
    # Let's test filling e8 after a snapshot command has run
    args1 = [
        "pwsh", "-NoProfile", "-Command",
        "npx -y agent-browser --json batch 'open https://my.digiseller.com/inside/ad.asp' 'snapshot -i' 'fill @e8 \"testuser\"' 'snapshot -i'"
    ]
    print("Running with snapshot then @e8...")
    res1 = subprocess.run(args1, capture_output=True, text=True, encoding="utf-8")
    with open("scratch/out1.txt", "w", encoding="utf-8") as f:
        f.write(res1.stdout + "\n" + res1.stderr)

    # Let's also test without the @ prefix, just 'fill e8' after a snapshot
    args2 = [
        "pwsh", "-NoProfile", "-Command",
        "npx -y agent-browser --json batch 'open https://my.digiseller.com/inside/ad.asp' 'snapshot -i' 'fill e8 \"testuser\"' 'snapshot -i'"
    ]
    print("Running with snapshot then e8...")
    res2 = subprocess.run(args2, capture_output=True, text=True, encoding="utf-8")
    with open("scratch/out2.txt", "w", encoding="utf-8") as f:
        f.write(res2.stdout + "\n" + res2.stderr)

    print("Done! Check out1.txt and out2.txt.")

if __name__ == "__main__":
    test_run()
