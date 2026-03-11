from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from oriexa_mcp.server import _client as mcp_client
from oriexa_mcp.server import (
    accept_user_claim,
    accept_user_deliverable,
    claim_task,
    create_user_task,
    get_user_task,
    get_user_tasks,
    login_user,
    register_user,
    request_user_revision,
    submit_deliverable,
)


@pytest_asyncio.fixture
async def mcp_http():
    transport = ASGITransport(app=app)
    test_http = AsyncClient(
        transport=transport,
        base_url="http://test/api/v1",
        headers={"Content-Type": "application/json"},
    )
    mcp_client._client = test_http
    try:
        yield
    finally:
        await test_http.aclose()
        mcp_client._client = None


@pytest.mark.asyncio
async def test_mcp_supports_self_serve_poster_lifecycle(mcp_http, agent_with_key):
    email = "poster-mcp@example.com"
    password = "password123"

    registered = await register_user(
        email=email,
        password=password,
        name="Poster MCP",
    )
    assert registered["email"] == email
    user_id = registered["id"]

    logged_in = await login_user(email=email, password=password)
    assert logged_in["id"] == user_id

    created = await create_user_task(
        user_id=user_id,
        title="Build MCP-ready task flow",
        description="Create an end-to-end task flow that an outside agent can complete through MCP.",
        budget_credits=120,
        category_id=1,
        requirements="Support poster registration, claiming, delivery, and acceptance.",
        max_revisions=2,
    )
    task_id = created["id"]
    assert task_id > 0

    user_tasks = await get_user_tasks(user_id=user_id)
    assert any(task["id"] == task_id for task in user_tasks)

    claim = await claim_task(
        api_key=agent_with_key["api_key"],
        task_id=task_id,
        proposed_credits=100,
        message="I can complete this through the MCP lifecycle.",
    )
    claim_id = claim["data"]["id"]

    accepted_claim = await accept_user_claim(
        user_id=user_id,
        task_id=task_id,
        claim_id=claim_id,
    )
    assert accepted_claim["success"] is True

    first_deliverable = await submit_deliverable(
        api_key=agent_with_key["api_key"],
        task_id=task_id,
        content="Initial deliverable from the worker agent.",
    )
    first_deliverable_id = first_deliverable["data"]["id"]

    revision = await request_user_revision(
        user_id=user_id,
        task_id=task_id,
        deliverable_id=first_deliverable_id,
        notes="Add a clearer explanation of the MCP handoff.",
    )
    assert revision["success"] is True

    final_deliverable = await submit_deliverable(
        api_key=agent_with_key["api_key"],
        task_id=task_id,
        content="Final deliverable with the MCP handoff clarified.",
    )
    final_deliverable_id = final_deliverable["data"]["id"]

    accepted_deliverable = await accept_user_deliverable(
        user_id=user_id,
        task_id=task_id,
        deliverable_id=final_deliverable_id,
    )
    assert accepted_deliverable["success"] is True

    task_detail = await get_user_task(user_id=user_id, task_id=task_id)
    assert task_detail["status"] == "completed"
    assert any(
        deliverable["id"] == final_deliverable_id and deliverable["status"] == "accepted"
        for deliverable in task_detail["deliverables"]
    )
