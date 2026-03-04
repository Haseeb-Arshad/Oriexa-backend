#!/usr/bin/env python3
"""
Tries every Supabase connection config and auto-updates .env with the first one that works.
Run from /opt/oriexa/repo:  python3 scripts/find_working_connection.py
"""
import asyncio
import os
import re
import sys

# ── read current DATABASE_URL from .env ──────────────────────────────────────
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
raw_url = ""
for line in open(ENV_PATH, encoding="utf-8"):
    if re.match(r"DATABASE_URL\s*=", line):
        raw_url = line.strip().split("=", 1)[1]
        break

# extract password from whatever URL is currently in .env
pw_match = re.search(r"://[^:]+:([^@]+)@", raw_url)
current_password = pw_match.group(1) if pw_match else ""

print(f"Current URL : {re.sub(r':([^:@]+)@', ':***@', raw_url)}")
print(f"Password    : {current_password[:4]}*** (from .env)\n")

PROJECT   = "qpdszbmoqxkytvrsbtsh"
DIRECT    = f"db.{PROJECT}.supabase.co"
POOLER    = "aws-0-ap-southeast-1.pooler.supabase.com"

# All configs to try: (label, host, port, user, password, ssl, statement_cache_size)
CANDIDATES = []
for pwd in [current_password]:          # add extra passwords here if you know them
    CANDIDATES += [
        # Pooler – transaction mode (6543) with project-prefixed user
        (f"Pooler tx   6543 postgres.{PROJECT}", POOLER, 6543, f"postgres.{PROJECT}", pwd, "require", 0),
        # Pooler – session mode (5432) with project-prefixed user
        (f"Pooler sess 5432 postgres.{PROJECT}", POOLER, 5432, f"postgres.{PROJECT}", pwd, "require", 0),
        # Direct connection (may fail if droplet has no IPv6)
        (f"Direct      5432 postgres",            DIRECT, 5432, "postgres",            pwd, "require", None),
        (f"Direct      5432 postgres (no ssl)",   DIRECT, 5432, "postgres",            pwd,  False,   None),
    ]


async def try_connect(host, port, user, password, ssl, statement_cache_size):
    try:
        import asyncpg
        kwargs = dict(host=host, port=port, user=user, password=password,
                      database="postgres", ssl=ssl, timeout=10)
        if statement_cache_size is not None:
            kwargs["statement_cache_size"] = statement_cache_size
        conn = await asyncpg.connect(**kwargs)
        ver = await conn.fetchval("SELECT version()")
        await conn.close()
        return True, ver
    except Exception as exc:
        return False, str(exc)


async def main():
    winner = None
    for label, host, port, user, pwd, ssl, scs in CANDIDATES:
        print(f"Testing: {label} ... ", end="", flush=True)
        ok, info = await try_connect(host, port, user, pwd, ssl, scs)
        if ok:
            print(f"OK  ({info[:60]})")
            winner = (host, port, user, pwd, ssl, scs)
            break
        else:
            short = info.replace("\n", " ")[:80]
            print(f"FAIL  {short}")

    if not winner:
        print("\nAll configs failed. Check your Supabase password on the dashboard:")
        print("  Project Settings -> Database -> Reset database password")
        print("Then run: sed -i 's|DATABASE_URL=.*|DATABASE_URL=<new-url>|' .env")
        sys.exit(1)

    host, port, user, pwd, ssl, scs = winner
    new_url = f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/postgres"
    print(f"\nWriting working URL to .env ...")

    content = open(ENV_PATH, encoding="utf-8").read()
    content = re.sub(r"DATABASE_URL=[^\n]*", f"DATABASE_URL={new_url}", content)
    open(ENV_PATH, "w", encoding="utf-8").write(content)
    print(f"Done. DATABASE_URL={re.sub(r':([^:@]+)@', ':***@', new_url)}")
    print("\nNow run:  .venv/bin/alembic upgrade head")


asyncio.run(main())
