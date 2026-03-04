"""Seed default system agents with deterministic API keys.

All agents share one operator user (system@oriexa.ai).
Keys are deterministic so .env can be pre-configured and re-runs are idempotent.

Agent roster:
  1. Oriexa Orchestrator  → ORIEXA_API_KEY
  2. Reviewer Agent         → REVIEWER_AGENT_API_KEY
  3. Coding Agent           → CODING_AGENT_API_KEY
  4. Writing Agent          → WRITING_AGENT_API_KEY
  5. Research Agent         → RESEARCH_AGENT_API_KEY
  6. Data Processing Agent  → DATA_AGENT_API_KEY
"""

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.db.models import Agent, User

logger = logging.getLogger(__name__)

OPERATOR_EMAIL = "system@oriexa.ai"
OPERATOR_NAME = "Oriexa System"

# th_agent_ (9 chars) + 64 hex chars = 73 chars total
# All chars are valid hex (0-9, a-f) and clearly structured
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
        "category_ids": [7],  # General
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
        "category_ids": [1],  # Coding
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
        "category_ids": [1],  # Coding
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
        "category_ids": [2],  # Writing
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
        "category_ids": [3],  # Research
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
        "category_ids": [4],  # Data Processing
        "hourly_rate": 55,
    },
]


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def seed_agents(session: AsyncSession) -> None:
    """Idempotently seed system agents. Safe to call on every startup."""

    # 1. Ensure operator user exists
    result = await session.execute(select(User.id).where(User.email == OPERATOR_EMAIL))
    row = result.first()
    if not row:
        pwd_hash = hash_password("system-not-for-login-oriexa-x9z2p4q7")
        user = User(
            email=OPERATOR_EMAIL,
            name=OPERATOR_NAME,
            password_hash=pwd_hash,
            role="operator",
            credit_balance=500,
        )
        session.add(user)
        await session.flush()
        operator_id = user.id
        logger.info("Created system operator user (id=%d)", operator_id)
    else:
        operator_id = row[0]

    # 2. Seed each agent idempotently (skip if api_key_hash already exists)
    seeded = 0
    for defn in AGENT_DEFINITIONS:
        key_hash = _hash_key(defn["key"])
        existing = await session.execute(select(Agent.id).where(Agent.api_key_hash == key_hash))
        if existing.first():
            continue

        agent = Agent(
            operator_id=operator_id,
            name=defn["name"],
            description=defn["description"],
            capabilities=defn["capabilities"],
            category_ids=defn["category_ids"],
            hourly_rate_credits=defn["hourly_rate"],
            api_key_hash=key_hash,
            api_key_prefix=defn["key"][:14],
            status="active",
        )
        session.add(agent)
        seeded += 1
        logger.info("Seeded agent: %s (prefix: %s)", defn["name"], defn["key"][:14])

    if seeded:
        await session.commit()
        logger.info("Agent seeding complete — %d agent(s) added.", seeded)
    else:
        logger.debug("Agent seeding: all agents already present.")
