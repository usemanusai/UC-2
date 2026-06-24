with open("validator_pro_v2.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    if "btn_discover" in line:
        print(f"Line {idx+1}: {line.strip()}")
