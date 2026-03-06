import os
import re

log_file = "nohup.out"

if not os.path.exists(log_file):
    print(f"Log file not found: {log_file}")
    
    # Let's search inside some common log locations
    for root, dirs, files in os.walk("."):
        for f in files:
            if f.endswith(".log") or f == "nohup.out":
                path = os.path.join(root, f)
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as fp:
                        content = fp.read()
                        if "vercel" in content.lower():
                            print(f"--- Found 'vercel' in {path} ---")
                            lines = content.split('\n')
                            for i, line in enumerate(lines):
                                if "vercel" in line.lower() and ("warning" in line.lower() or "error" in line.lower() or "fail" in line.lower()):
                                    start = max(0, i - 2)
                                    end = min(len(lines), i + 5)
                                    print("\n".join(lines[start:end]))
                                    print("-" * 40)
                except Exception:
                    pass
else:
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as fp:
        lines = fp.readlines()
        for i, line in enumerate(lines):
            if "vercel" in line.lower() and ("warning" in line.lower() or "error" in line.lower() or "fail" in line.lower()):
                start = max(0, i - 2)
                end = min(len(lines), i + 5)
                print("".join(lines[start:end]))
                print("-" * 40)
