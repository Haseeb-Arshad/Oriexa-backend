"""Test fixtures: async client, test DB, seeded data."""

import asyncio
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Override env vars before importing app
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://oriexa:oriexa@localhost:5432/oriexa_test")
os.environ.setdefault("NEXTAUTH_SECRET", "test-secret")
os.environ.setdefault("ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("ENVIRONMENT", "test")

from app.db.engine import get_db
from app.db.models import Base
from app.main import app

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create all tables and seed categories once for the test session."""
    async with test_engine.begin() as conn:
        # Drop and recreate all tables
        await conn.run_sync(Base.metadata.drop_all)

        # Create enum types first
        for enum_sql in [
            "DROP TYPE IF EXISTS user_role CASCADE",
            "DROP TYPE IF EXISTS agent_status CASCADE",
            "DROP TYPE IF EXISTS task_status CASCADE",
            "DROP TYPE IF EXISTS claim_status CASCADE",
            "DROP TYPE IF EXISTS deliverable_status CASCADE",
            "DROP TYPE IF EXISTS transaction_type CASCADE",
            "DROP TYPE IF EXISTS webhook_event CASCADE",
            "DROP TYPE IF EXISTS llm_provider CASCADE",
            "DROP TYPE IF EXISTS review_result CASCADE",
            "DROP TYPE IF EXISTS review_key_source CASCADE",
            "DROP TYPE IF EXISTS orch_task_status CASCADE",
            "DROP TYPE IF EXISTS agent_role CASCADE",
            "DROP TYPE IF EXISTS subtask_status CASCADE",
            "DROP TYPE IF EXISTS message_direction CASCADE",
            "CREATE TYPE user_role AS ENUM ('poster', 'operator', 'both', 'admin')",
            "CREATE TYPE agent_status AS ENUM ('active', 'paused', 'suspended')",
            "CREATE TYPE task_status AS ENUM ('open', 'claimed', 'in_progress', 'delivered', 'completed', 'cancelled', 'disputed')",
            "CREATE TYPE claim_status AS ENUM ('pending', 'accepted', 'rejected', 'withdrawn')",
            "CREATE TYPE deliverable_status AS ENUM ('submitted', 'accepted', 'rejected', 'revision_requested')",
            "CREATE TYPE transaction_type AS ENUM ('deposit', 'bonus', 'payment', 'platform_fee', 'refund')",
            "CREATE TYPE webhook_event AS ENUM ('task.new_match', 'claim.accepted', 'claim.rejected', 'deliverable.accepted', 'deliverable.revision_requested')",
            "CREATE TYPE llm_provider AS ENUM ('openrouter', 'openai', 'anthropic')",
            "CREATE TYPE review_result AS ENUM ('pass', 'fail', 'pending', 'skipped')",
            "CREATE TYPE review_key_source AS ENUM ('poster', 'freelancer', 'none')",
            "CREATE TYPE orch_task_status AS ENUM ('pending', 'claiming', 'clarifying', 'planning', 'executing', 'reviewing', 'delivering', 'completed', 'failed')",
            "CREATE TYPE agent_role AS ENUM ('triage', 'clarification', 'planning', 'execution', 'complex_task', 'review')",
            "CREATE TYPE subtask_status AS ENUM ('pending', 'in_progress', 'completed', 'failed', 'skipped')",
            "CREATE TYPE message_direction AS ENUM ('agent_to_poster', 'poster_to_agent')",
        ]:
            await conn.execute(text(enum_sql))

        await conn.run_sync(Base.metadata.create_all)

    # Seed categories
    async with test_session_factory() as session:
        from app.db.seed import seed_categories
        await seed_categories(session)

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables():
    """Truncate data tables between tests (keep categories)."""
    yield
    async with test_session_factory() as session:
        for table in [
            "orch_agent_runs", "orch_messages", "orch_subtasks", "orch_task_executions",
            "submission_attempts", "idempotency_keys", "webhook_deliveries",
            "webhooks", "credit_transactions", "reviews", "deliverables",
            "task_claims", "tasks", "agents", "users",
        ]:
            await session.execute(text(f"TRUNCATE {table} RESTART IDENTITY CASCADE"))
        await session.commit()

    # Reset rate limiter and auth cache
    from app.middleware.rate_limit import reset_store
    from app.auth.dependencies import clear_auth_cache
    reset_store()
    clear_auth_cache()


async def _override_get_db():
    async with test_session_factory() as session:
        yield session


app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient):
    """Register a user and return their info."""
    resp = await client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "password123",
        "name": "Test User",
    })
    assert resp.status_code == 201
    return resp.json()


@pytest_asyncio.fixture
async def agent_with_key(client: AsyncClient, registered_user):
    """Register an agent and return agent info with raw API key."""
    resp = await client.post("/api/v1/agents", json={
        "email": "test@example.com",
        "password": "password123",
        "name": "Test Agent",
        "description": "A test agent for automated testing purposes",
        "capabilities": ["coding", "testing"],
    })
    assert resp.status_code == 200 or resp.status_code == 201
    data = resp.json()
    if "data" in data:
        data = data["data"]
    return data


@pytest_asyncio.fixture
async def auth_headers(agent_with_key):
    """Return Bearer auth headers for the test agent."""
    return {"Authorization": f"Bearer {agent_with_key['api_key']}"}


@pytest_asyncio.fixture
async def open_task(client: AsyncClient, auth_headers):
    """Create and return an open task."""
    resp = await client.post(
        "/api/v1/tasks",
        json={
            "title": "Test Task for Testing",
            "description": "This is a test task with enough description length for validation",
            "budget_credits": 100,
            "category_id": 1,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["data"]
