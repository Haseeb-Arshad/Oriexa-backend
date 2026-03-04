#!/usr/bin/env python3
"""Seed default system agents with deterministic API keys.

Creates operator user system@oriexa.ai and registers 6 system agents:
  1. Oriexa Orchestrator  → ORIEXA_API_KEY
  2. Reviewer Agent         → REVIEWER_AGENT_API_KEY
  3. Coding Agent           → CODING_AGENT_API_KEY
  4. Writing Agent          → WRITING_AGENT_API_KEY
  5. Research Agent         → RESEARCH_AGENT_API_KEY
  6. Data Processing Agent  → DATA_AGENT_API_KEY

Usage:
    python scripts/seed_agents.py
    python scripts/seed_agents.py --dry-run
"""

import argparse
import hashlib
import os
import sys

# Load DATABASE_URL from .env if not already set
_env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_file):
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.", file=sys.stderr)
    sys.exit(1)

# Convert asyncpg URL to psycopg-compatible URL
_sync_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

try:
    import psycopg
except ImportError:
    print("ERROR: psycopg not installed. Run: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)

# ── Agent definitions (must match app/db/seed_agents.py) ──────────────────────
OPERATOR_EMAIL = "system@oriexa.ai"
OPERATOR_NAME = "Oriexa System"

AGENT_DEFINITIONS = [
    {
        "env_var": "ORIEXA_API_KEY",
        "key": "th_agent_4cbd9fbde50424cedc203c7f48958c8138949cab5d7d3a181cfde35dc08f71d2",
        "name": "Oriexa Orchestrator",
        "description": (
            "Core orchestration agent that monitors the task queue, routes work to "
            "specialized sub-agents, and manages the full execution pipeline from "
            "task pickup to delivery."
        ),
        "capabilities": ["task_routing", "orchestration", "pipeline_management", "monitoring"],
        "category_ids": [7],
        "hourly_rate": 10,
    },
    {
        "env_var": "REVIEWER_AGENT_API_KEY",
        "key": "th_agent_baf0a258db6095f68ab514cb16dd29927c5dfa606953916346e2c34fb08700a2",
        "name": "Reviewer Agent",
        "description": (
            "Specialized agent for code review, quality assurance, and automated testing. "
            "Evaluates deliverables, identifies bugs, suggests improvements, and enforces coding standards."
        ),
        "capabilities": ["code_review", "quality_assurance", "automated_testing", "feedback", "linting"],
        "category_ids": [1],
        "hourly_rate": 50,
    },
    {
        "env_var": "CODING_AGENT_API_KEY",
        "key": "th_agent_e31c9d408361ab222c5668af6c48fc0484300759ffa350942e58df0111069b94",
        "name": "Coding Agent",
        "description": (
            "Frontend specialist that builds websites and web apps using vanilla HTML/CSS/JavaScript "
            "and Next.js. Preferred models: z-ai/glm-5 (default), openai/gpt-5.3-codex (complex tasks). "
            "Planning uses anthropic/claude-sonnet-4.6 for strong architectural reasoning."
        ),
        "capabilities": [
            "html", "css", "vanilla_js", "nextjs", "react",
            "typescript", "frontend", "responsive_design", "ui_components",
        ],
        "category_ids": [1],
        "hourly_rate": 80,
    },
    {
        "env_var": "WRITING_AGENT_API_KEY",
        "key": "th_agent_23cf36e629f4d547433084db564f9e075ab23937d1f71d08210dbf6ffb5adf76",
        "name": "Writing Agent",
        "description": (
            "Professional writing agent for blog posts, technical documentation, copywriting, "
            "marketing content, and proofreading. Produces clear, engaging, well-structured content."
        ),
        "capabilities": ["copywriting", "blog_posts", "technical_documentation", "proofreading", "seo_writing"],
        "category_ids": [2],
        "hourly_rate": 40,
    },
    {
        "env_var": "RESEARCH_AGENT_API_KEY",
        "key": "th_agent_d927bcc2f0ce4e9e43fe930dbc348c23062a9debeb9bdf19c7d056bed0c972ae",
        "name": "Research Agent",
        "description": (
            "Research and analysis agent capable of web research, competitive analysis, "
            "data synthesis, fact-checking, and producing detailed reports with citations."
        ),
        "capabilities": ["web_research", "data_analysis", "summarization", "fact_checking", "report_writing"],
        "category_ids": [3],
        "hourly_rate": 45,
    },
    {
        "env_var": "DATA_AGENT_API_KEY",
        "key": "th_agent_d43b6c659879a9f161475bfa321fd82e0d23331b272fa2a7432ef5ee5220e8ac",
        "name": "Data Processing Agent",
        "description": (
            "Data processing and transformation agent specializing in ETL pipelines, "
            "CSV/JSON processing, database queries, data cleaning, and generating structured datasets."
        ),
        "capabilities": ["data_cleaning", "etl", "csv_processing", "json_transformation", "sql_queries"],
        "category_ids": [4],
        "hourly_rate": 55,
    },
]


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def run(dry_run: bool = False) -> None:
    print(f"Connecting to DB (dry_run={dry_run})")
    with psycopg.connect(_sync_url, autocommit=False) as conn:
        with conn.cursor() as cur:

            # Step 1: Ensure operator user exists
            print(f"  [Operator user: {OPERATOR_EMAIL}]", end=" ", flush=True)
            if not dry_run:
                cur.execute(
                    """
                    INSERT INTO users (email, name, role, credit_balance)
                    VALUES (%s, %s, 'operator', 500)
                    ON CONFLICT (email) DO NOTHING
                    """,
                    (OPERATOR_EMAIL, OPERATOR_NAME),
                )
                cur.execute("SELECT id FROM users WHERE email = %s", (OPERATOR_EMAIL,))
                operator_id = cur.fetchone()[0]
                print(f"OK (id={operator_id})")
            else:
                operator_id = 0
                print("(skipped)")

            # Step 2: Seed each agent (check first — idempotent without ON CONFLICT)
            for defn in AGENT_DEFINITIONS:
                key_hash = _hash_key(defn["key"])
                print(f"  [Agent: {defn['name']}]", end=" ", flush=True)
                if not dry_run:
                    cur.execute(
                        "SELECT id FROM agents WHERE api_key_hash = %s", (key_hash,)
                    )
                    if cur.fetchone():
                        print("already exists — skipped")
                        continue
                    cur.execute(
                        """
                        INSERT INTO agents (
                            operator_id, name, description, capabilities, category_ids,
                            hourly_rate_credits, api_key_hash, api_key_prefix, status
                        ) VALUES (
                            %s, %s, %s, %s::text[], %s::int[], %s, %s, %s, 'active'
                        )
                        """,
                        (
                            operator_id,
                            defn["name"],
                            defn["description"],
                            defn["capabilities"],
                            defn["category_ids"],
                            defn["hourly_rate"],
                            key_hash,
                            defn["key"][:14],
                        ),
                    )
                    print(f"OK  (prefix: {defn['key'][:14]})")
                else:
                    print(f"(skipped — would insert, prefix: {defn['key'][:14]})")

            if not dry_run:
                conn.commit()
            else:
                conn.rollback()

    print()
    if not dry_run:
        print("All done — agents seeded.")
    else:
        print("Dry run complete — no changes made.")

    print()
    print("Agent API keys (copy to .env):")
    print("-" * 72)
    for defn in AGENT_DEFINITIONS:
        print(f"{defn['env_var']}={defn['key']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed default system agents")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
