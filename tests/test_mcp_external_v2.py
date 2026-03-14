from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from oriexa_mcp.server import _client as mcp_client
from oriexa_mcp.server import external_mcp


@pytest_asyncio.fixture
async def external_call():
    transport = ASGITransport(app=app)
    test_http = AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Content-Type": "application/json"},
    )
    previous_base_url = mcp_client._base_url
    mcp_client._client = test_http
    mcp_client._base_url = "http://test"
    tools = external_mcp._tool_manager._tools

    async def _call(tool_name: str, **kwargs) -> dict:
        return await tools[tool_name].fn(**kwargs)

    try:
        yield _call
    finally:
        await test_http.aclose()
        mcp_client._client = None
        mcp_client._base_url = previous_base_url


def test_external_mcp_registers_v2_tools():
    tool_names = set(external_mcp._tool_manager._tools.keys())

    assert "bootstrap_actor" in tool_names
    assert "create_task" in tool_names
    assert "list_tasks" in tool_names
    assert "claim_task" in tool_names
    assert "accept_claim" in tool_names
    assert "submit_deliverable" in tool_names
    assert "request_revision" in tool_names
    assert "accept_deliverable" in tool_names
    assert "send_message" in tool_names
    assert "answer_question" in tool_names
    assert "register_webhook" in tool_names


@pytest.mark.asyncio
async def test_external_mcp_v2_lifecycle(external_call):
    poster_bootstrap = await external_call(
        "bootstrap_actor",
        email="external-poster-mcp@example.com",
        password="password123",
        scope="poster",
        name="External Poster",
    )
    worker_bootstrap = await external_call(
        "bootstrap_actor",
        email="external-worker-mcp@example.com",
        password="password123",
        scope="worker",
        name="External Worker",
        agent_name="External Worker Agent",
        capabilities=["api", "mcp"],
        category_ids=[1],
    )

    poster_token = poster_bootstrap["data"]["token"]
    worker_token = worker_bootstrap["data"]["token"]

    created = await external_call(
        "create_task",
        automation_token=poster_token,
        title="External MCP v2 task",
        description="Validate the outside-agent lifecycle entirely through the v2 MCP surface.",
        budget_credits=140,
        category_id=1,
        requirements="Support question answering, revisions, and completion.",
        max_revisions=2,
    )
    task_id = created["data"]["id"]
    assert created["data"]["workflow"]["phase"] == "marketplace_open"

    marketplace = await external_call(
        "list_tasks",
        automation_token=worker_token,
        view="marketplace",
    )
    marketplace_task = next(item for item in marketplace["data"]["items"] if item["id"] == task_id)
    assert "claim_task" in marketplace_task["workflow"]["next_actions"]

    claim = await external_call(
        "claim_task",
        automation_token=worker_token,
        task_id=task_id,
        proposed_credits=120,
        message="I can complete this through the external MCP contract.",
    )
    claim_id = claim["data"]["claims"][0]["id"]
    assert claim["data"]["workflow"]["phase"] == "awaiting_claim_acceptance"

    accepted_claim = await external_call(
        "accept_claim",
        automation_token=poster_token,
        task_id=task_id,
        claim_id=claim_id,
    )
    assert accepted_claim["data"]["status"] == "claimed"
    assert accepted_claim["data"]["workflow"]["awaiting_actor"] == "worker"

    question = await external_call(
        "send_message",
        automation_token=worker_token,
        task_id=task_id,
        content="Which deployment target should I optimize for?",
        message_type="question",
        structured_data={
            "question_id": "deploy-target",
            "options": ["cloud-run", "ecs"],
        },
    )
    question_message_id = question["data"]["messages"][-1]["id"]

    answered = await external_call(
        "answer_question",
        automation_token=poster_token,
        task_id=task_id,
        message_id=question_message_id,
        response="Optimize for Cloud Run first.",
        option_index=0,
    )
    assert answered["data"]["workflow"]["latest_message"]["parent_id"] == question_message_id

    first_deliverable = await external_call(
        "submit_deliverable",
        automation_token=worker_token,
        task_id=task_id,
        content="Initial external MCP deliverable.",
    )
    first_deliverable_id = first_deliverable["data"]["deliverables"][0]["id"]
    assert first_deliverable["data"]["status"] == "delivered"

    revised = await external_call(
        "request_revision",
        automation_token=poster_token,
        task_id=task_id,
        deliverable_id=first_deliverable_id,
        notes="Please clarify the deployment section.",
    )
    assert revised["data"]["status"] == "in_progress"
    assert revised["data"]["workflow"]["awaiting_actor"] == "worker"

    final_deliverable = await external_call(
        "submit_deliverable",
        automation_token=worker_token,
        task_id=task_id,
        content="Final external MCP deliverable with deployment notes clarified.",
    )
    final_deliverable_id = final_deliverable["data"]["deliverables"][0]["id"]

    completed = await external_call(
        "accept_deliverable",
        automation_token=poster_token,
        task_id=task_id,
        deliverable_id=final_deliverable_id,
    )
    assert completed["data"]["status"] == "completed"
    assert completed["data"]["workflow"]["phase"] == "completed"

    state = await external_call(
        "get_task_state",
        automation_token=poster_token,
        task_id=task_id,
    )
    assert state["data"]["workflow"]["phase"] == "completed"
    assert state["data"]["workflow"]["next_actions"] == []

    created_webhook = await external_call(
        "register_webhook",
        automation_token=worker_token,
        url="https://example.com/external-v2-webhook",
        events=["task.updated", "message.created"],
    )
    webhook_id = created_webhook["data"]["id"]

    webhook_list = await external_call("list_webhooks", automation_token=worker_token)
    assert any(item["id"] == webhook_id for item in webhook_list["data"])

    deleted = await external_call(
        "delete_webhook",
        automation_token=worker_token,
        webhook_id=webhook_id,
    )
    assert deleted["data"]["deleted"] is True
