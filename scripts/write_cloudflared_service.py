#!/usr/bin/env python3
"""Write the cloudflared named-tunnel systemd service file."""
import os, subprocess

content = (
    "[Unit]\n"
    "Description=Cloudflare Tunnel (Oriexa API)\n"
    "After=network.target oriexa-api.service\n"
    "\n"
    "[Service]\n"
    "User=root\n"
    "ExecStart=/usr/bin/cloudflared tunnel run\n"
    "Restart=always\n"
    "RestartSec=5\n"
    "\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
)

path = "/etc/systemd/system/cloudflared.service"
open(path, "w").write(content)
print("Written to", path)
print()
print(content)

subprocess.run(["systemctl", "daemon-reload"])
subprocess.run(["systemctl", "restart", "cloudflared"])
print("Service restarted.")
