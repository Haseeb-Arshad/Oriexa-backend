"""Test fixtures: async client, fresh schema, and seeded actors."""

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Override env vars before importing app
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/oriexa_test")
os.environ.setdefault("NEXTAUTH_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("EXTERNAL_TOKEN_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("ENVIRONMENT", "test")

from app.auth.api_key import generate_api_key
from app.db import models as db_models
from app.db.engine import get_db
from app.db.models import Agent, Base
from app.main import app

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
async_test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
# Backward-compatible name used by legacy orchestrator bridge tests.
test_session_factory = async_test_session_factory
test_session_factory.__test__ = False


ENUM_TYPES = [
    db_models.user_role_enum,
    db_models.agent_status_enum,
    db_models.task_status_enum,
    db_models.claim_status_enum,
    db_models.deliverable_status_enum,
    db_models.transaction_type_enum,
    db_models.webhook_event_enum,
    db_models.llm_provider_enum,
    db_models.review_result_enum,
    db_models.review_key_source_enum,
    db_models.orch_task_status_enum,
    db_models.agent_role_enum,
    db_models.subtask_status_enum,
    db_models.message_direction_enum,
    db_models.task_msg_sender_type_enum,
    db_models.task_msg_type_enum,
]


async def _recreate_schema() -> None:
    await test_engine.dispose()
    async with test_engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

        for enum_type in ENUM_TYPES:
            values = ", ".join(f"'{value}'" for value in enum_type.enums)
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_type.name} CASCADE"))
            await conn.execute(text(f"CREATE TYPE {enum_type.name} AS ENUM ({values})"))

        await conn.run_sync(Base.metadata.create_all)

    async with async_test_session_factory() as session:
        from app.db.seed import seed_categories

        await seed_categories(session)
    await test_engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def setup_database():
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def reset_database():
    await _recreate_schema()
    from app.auth.dependencies import clear_auth_cache
    from app.middleware.rate_limit import reset_store

    reset_store()
    clear_auth_cache()
    yield


async def _override_get_db():
    async with async_test_session_factory() as session:
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
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "password": "password123",
            "name": "Test User",
        },
    )
    assert resp.status_code == 201
    return resp.json()


@pytest_asyncio.fixture
async def agent_with_key(client: AsyncClient, registered_user):
    """Create an agent directly in DB and return info with raw API key."""
    key_info = generate_api_key()
    async with async_test_session_factory() as session:
        agent = Agent(
            operator_id=registered_user["id"],
            name="Test Agent",
            description="A test agent for automated testing purposes",
            capabilities=["coding", "testing"],
            api_key_hash=key_info["hash"],
            api_key_prefix=key_info["prefix"],
            status="active",
        )
        session.add(agent)
        await session.flush()
        await session.commit()

        return {
            "agent_id": agent.id,
            "api_key": key_info["raw_key"],
            "api_key_prefix": key_info["prefix"],
            "operator_id": registered_user["id"],
            "name": agent.name,
            "description": agent.description,
            "capabilities": agent.capabilities,
        }


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
