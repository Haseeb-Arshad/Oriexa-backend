#!/usr/bin/env python3
"""Write a clean cloudflared config.yml with no indentation issues."""
import os

UUID = "5051ca32-668a-4a61-9b9b-b118b4bfbd66"
HOSTNAME = "oriexa.sayings.me"

content = (
    "tunnel: " + UUID + "\n"
    "credentials-file: /root/.cloudflared/" + UUID + ".json\n"
    "\n"
    "ingress:\n"
    "  - hostname: " + HOSTNAME + "\n"
    "    service: http://localhost:8000\n"
    "  - service: http_status:404\n"
)

path = os.path.expanduser("~/.cloudflared/config.yml")
os.makedirs(os.path.dirname(path), exist_ok=True)
open(path, "w").write(content)

print("Written to", path)
print()
print(content)
