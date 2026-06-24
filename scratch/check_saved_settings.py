import pickle
import os

settings_file = os.path.join("engine", "registry", "settings.pkl")
if os.path.exists(settings_file):
    with open(settings_file, "rb") as f:
        settings = pickle.load(f)
    print("SAVED SETTINGS KEYS:")
    for k, v in settings.items():
        if "key" in k.lower():
            # Mask keys for safety but show presence/length
            print(f"  {k}: {v[:5]}... ({len(v)} chars)" if v else f"  {k}: Empty")
        else:
            print(f"  {k}: {v}")
else:
    print(f"Settings file {settings_file} not found.")
