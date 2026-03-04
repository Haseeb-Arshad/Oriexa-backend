#!/usr/bin/env python3
"""
Reset all user-generated transactional data from the DB.
KEEPS:  categories, agents, webhooks, and operator users (those who own agents).
CLEARS: tasks, claims, deliverables, reviews, credit_transactions,
        submission_attempts, webhook_deliveries, idempotency_keys,
        and all orchestrator tracking tables.

Usage:
    python scripts/reset_user_data.py
    python scripts/reset_user_data.py --dry-run
"""

import argparse
import os
import sys

# Load DATABASE_URL from .env if not already set
env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_file):
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.", file=sys.stderr)
    sys.exit(1)

# Convert asyncpg URL to psycopg3-compatible URL
sync_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

try:
    import psycopg
except ImportError:
    print("ERROR: psycopg not installed. Run: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)

# SQL steps in FK-safe order (children before parents)
STEPS = [
    # ── Orchestrator tracking (full clear — no user data risk) ──────────────
    ("Clear orch_agent_runs",        "DELETE FROM orch_agent_runs"),
    ("Clear orch_messages",          "DELETE FROM orch_messages"),
    ("Clear orch_subtasks",          "DELETE FROM orch_subtasks"),
    ("Clear orch_task_executions",   "DELETE FROM orch_task_executions"),
    # ── Transactional workflow data ─────────────────────────────────────────
    ("Clear submission_attempts",    "DELETE FROM submission_attempts"),
    ("Clear reviews",                "DELETE FROM reviews"),
    ("Clear webhook_deliveries",     "DELETE FROM webhook_deliveries"),
    ("Clear idempotency_keys",       "DELETE FROM idempotency_keys"),
    ("Clear credit_transactions",    "DELETE FROM credit_transactions"),
    ("Clear deliverables",           "DELETE FROM deliverables"),
    ("Clear task_messages",          "DELETE FROM task_messages"),
    ("Clear task_claims",            "DELETE FROM task_claims"),
    ("Clear tasks",                  "DELETE FROM tasks"),
    # ── Users: remove non-operators ────────────────────────────────────────
    ("Remove non-operator users",
     "DELETE FROM users WHERE id NOT IN (SELECT DISTINCT operator_id FROM agents)"),
    # ── Reset operator user balances to 100 (agent registration bonus) ──────
    ("Reset operator credit balances",
     "UPDATE users SET credit_balance = 100 WHERE id IN (SELECT DISTINCT operator_id FROM agents)"),
    # ── Reset sequences so IDs restart from 1 ───────────────────────────────
    ("Reset sequences", """
        SELECT setval('orch_task_executions_id_seq', 1, false);
        SELECT setval('orch_subtasks_id_seq',        1, false);
        SELECT setval('orch_messages_id_seq',        1, false);
        SELECT setval('orch_agent_runs_id_seq',      1, false);
        SELECT setval('submission_attempts_id_seq',  1, false);
        SELECT setval('reviews_id_seq',              1, false);
        SELECT setval('webhook_deliveries_id_seq',   1, false);
        SELECT setval('idempotency_keys_id_seq',     1, false);
        SELECT setval('credit_transactions_id_seq',  1, false);
        SELECT setval('deliverables_id_seq',         1, false);
        SELECT setval('task_claims_id_seq',          1, false);
        SELECT setval('tasks_id_seq',                1, false);
        SELECT setval('task_messages_id_seq',        1, false);
    """),
]


def run(dry_run: bool = False) -> None:
    print(f"Connecting to DB (dry_run={dry_run})")
    with psycopg.connect(sync_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            for label, sql in STEPS:
                print(f"  [{label}]", end=" ", flush=True)
                if dry_run:
                    print("(skipped)")
                    continue
                for statement in sql.strip().split(";"):
                    statement = statement.strip()
                    if statement:
                        cur.execute(statement)
                        if cur.rowcount >= 0:
                            print(f"{cur.rowcount} rows", end=" ")
                print("OK")

            if not dry_run:
                conn.commit()
                print("\nAll done — DB reset committed.")
            else:
                conn.rollback()
                print("\nDry run complete — no changes made.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset user transactional data")
    parser.add_argument("--dry-run", action="store_true", help="Print steps without executing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
