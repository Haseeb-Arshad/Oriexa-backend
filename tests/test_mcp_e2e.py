"""End-to-end test for the current legacy MCP tool surface."""

from __future__ import annotations

import inspect

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from oriexa_mcp.errors import OriexaAPIError
from oriexa_mcp.server import _client as mcp_client
from oriexa_mcp.server import mcp as mcp_server


@pytest_asyncio.fixture
async def call(agent_with_key):
    api_key = agent_with_key["api_key"]
    operator_id = agent_with_key.get("operator_id", 1)
    transport = ASGITransport(app=app)
    test_http = AsyncClient(
        transport=transport,
        base_url="http://test/api/v1",
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-User-ID": str(operator_id),
            "Content-Type": "application/json",
        },
    )
    mcp_client._client = test_http
    tools = mcp_server._tool_manager._tools

    async def _call(tool_name: str, **kwargs) -> str:
        tool = tools[tool_name].fn
        signature = inspect.signature(tool)
        if "api_key" in signature.parameters and "api_key" not in kwargs:
            kwargs["api_key"] = api_key
        if "user_id" in signature.parameters and "user_id" not in kwargs:
            kwargs["user_id"] = operator_id
        return await tool(**kwargs)

    try:
        yield _call
    finally:
        await test_http.aclose()
        mcp_client._client = None


def test_legacy_mcp_registers_current_tool_names():
    tool_names = set(mcp_server._tool_manager._tools.keys())

    assert "create_task" in tool_names
    assert "claim_task" in tool_names
    assert "accept_claim" in tool_names
    assert "submit_deliverable" in tool_names
    assert "accept_deliverable" in tool_names
    assert "register_webhook" in tool_names
    assert "oriexa_create_task" not in tool_names


@pytest.mark.asyncio
async def test_legacy_mcp_full_lifecycle(call):
    profile = await call("get_my_profile")
    assert profile["ok"] is True
    assert profile["data"]["name"] == "Test Agent"

    updated = await call("update_my_profile", description="Updated via MCP E2E test")
    assert updated["ok"] is True

    credits_before = await call("get_my_credits")
    assert credits_before["ok"] is True

    created = await call(
        "create_user_task",
        title="Build a REST API",
        description=(
            "Create a comprehensive REST API with authentication, "
            "rate limiting, and full documentation."
        ),
        budget_credits=100,
        category_id="1",
    )
    task_id = created["id"]

    user_tasks = await call("get_user_tasks")
    assert any(task["id"] == task_id for task in user_tasks)

    detail = await call("get_user_task", task_id=task_id)
    assert detail["title"] == "Build a REST API"
    assert detail["status"] == "open"

    browse_open = await call("browse_tasks", status="open")
    assert any(task["id"] == task_id for task in browse_open["data"])

    claimed = await call(
        "claim_task",
        task_id=task_id,
        proposed_credits=90,
        message="I can build this API",
    )
    assert claimed["ok"] is True
    claim_id = claimed["data"]["id"]

    claims_list = await call("list_task_claims", task_id=task_id)
    assert any(claim["id"] == claim_id for claim in claims_list["data"])

    accepted = await call("accept_user_claim", task_id=task_id, claim_id=claim_id)
    assert accepted["success"] is True

    delivered = await call(
        "submit_deliverable",
        task_id=task_id,
        content="Here is the completed REST API with all endpoints.",
    )
    assert delivered["ok"] is True
    deliverable_id = delivered["data"]["id"]

    deliverables = await call("list_task_deliverables", task_id=task_id)
    assert any(item["id"] == deliverable_id for item in deliverables["data"])

    revision = await call(
        "request_user_revision",
        task_id=task_id,
        deliverable_id=deliverable_id,
        notes="Please add rate-limiting documentation.",
    )
    assert revision["success"] is True

    delivered_again = await call(
        "submit_deliverable",
        task_id=task_id,
        content="Updated REST API with rate-limiting documentation added.",
    )
    assert delivered_again["ok"] is True
    final_deliverable_id = delivered_again["data"]["id"]

    accepted_deliverable = await call(
        "accept_user_deliverable",
        task_id=task_id,
        deliverable_id=final_deliverable_id,
    )
    assert accepted_deliverable["success"] is True

    credits_after = await call("get_my_credits")
    assert credits_after["ok"] is True

    webhook_created = await call(
        "register_webhook",
        url="https://example.com/webhook",
        events=["task.new_match", "claim.accepted"],
    )
    assert webhook_created["ok"] is True
    webhook_id = webhook_created["data"]["id"]

    webhook_list = await call("list_webhooks")
    assert any(item["id"] == webhook_id for item in webhook_list["data"])

    webhook_deleted = await call("delete_webhook", webhook_id=webhook_id)
    assert webhook_deleted["ok"] is True

    bulk_task_ids: list[int] = []
    for index in range(2):
        bulk_task = await call(
            "create_user_task",
            title=f"Bulk task {index + 1}",
            description=(
                f"Bulk test task number {index + 1} with sufficient description "
                "text for validation requirements."
            ),
            budget_credits=50,
            category_id="1",
        )
        bulk_task_ids.append(bulk_task["id"])

    bulk = await call(
        "bulk_claim_tasks",
        claims=[
            {
                "task_id": task_id_value,
                "proposed_credits": 40,
                "message": f"Bulk claim for task {task_id_value}",
            }
            for task_id_value in bulk_task_ids
        ],
    )
    assert bulk["ok"] is True
    assert bulk["data"]["summary"]["succeeded"] == len(bulk_task_ids)

    with pytest.raises(OriexaAPIError):
        await call("get_task", task_id="99999")
