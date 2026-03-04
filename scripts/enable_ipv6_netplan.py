#!/usr/bin/env python3
import re

addr = "2604:a880:800:14:0:2:9b58:b000"
gw   = "2604:a880:800:14::1"
path = "/etc/netplan/50-cloud-init.yaml"

c = open(path).read()
print("Before:\n", c)

if addr not in c:
    c = re.sub(r"(addresses:\n)", rf"\1            - {addr}/64\n", c, count=1)

if "::/0" not in c:
    c = re.sub(r"(routes:\n)", rf"\1            -   to: ::/0\n                via: {gw}\n", c, count=1)

open(path, "w").write(c)
print("After:\n", c)
print("Done — now run: sudo netplan apply")
