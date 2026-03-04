#!/usr/bin/env python3
"""Remove garbage lines (shell commands, etc.) from .env — keeps only KEY=VALUE lines."""
import re, os, shutil

env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
backup = env_path + ".bak"
shutil.copy(env_path, backup)
print(f"Backed up to {backup}")

lines = open(env_path, encoding="utf-8").readlines()
clean = []
for line in lines:
    stripped = line.strip()
    # Keep blank lines, comments, and valid KEY=VALUE lines
    if stripped == "" or stripped.startswith("#") or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', stripped):
        clean.append(line)
    else:
        print(f"  Removing: {stripped[:80]}")

open(env_path, "w", encoding="utf-8").writelines(clean)
print(f"Done — kept {len(clean)}/{len(lines)} lines")
