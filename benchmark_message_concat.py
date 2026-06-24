import timeit
import random

# Mock data
captured_info = {f"key_{i}": f"value_{i}" for i in range(1000)}
email = "test@example.com"
password = "password123"

def original():
    message = f"✅ Valid Account Found:\nEmail: {email}\nPassword: {password}\n"
    for key, value in captured_info.items():
        if key.lower() in ['inner_html', 'outer_html']:
            continue
        if value not in ("Not found", "Not captured"):
            message += f"{key.capitalize()}: {value}\n"
    return message

def optimized():
    message_parts = [f"✅ Valid Account Found:\nEmail: {email}\nPassword: {password}\n"]
    for key, value in captured_info.items():
        if key.lower() in ['inner_html', 'outer_html']:
            continue
        if value not in ("Not found", "Not captured"):
            message_parts.append(f"{key.capitalize()}: {value}\n")
    message = "".join(message_parts)
    return message

# Verify correctness
assert original() == optimized()

# Benchmark
n = 10000
orig_time = timeit.timeit(original, number=n)
opt_time = timeit.timeit(optimized, number=n)

print(f"Original: {orig_time:.5f}s")
print(f"Optimized: {opt_time:.5f}s")
print(f"Improvement: {(orig_time - opt_time) / orig_time * 100:.2f}%")
