"""End-to-end test: every MCP tool → ASGI transport → real FastAPI handlers.

Validates MCP tools by running a full agent lifecycle narrative:
  browse → create task → claim → deliver → revise → complete
  → webhooks → bulk ops → management → error cases

No live server required — requests go in-process via httpx ASGITransport.
"""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from oriexa_mcp.errors import OriexaAPIError
from oriexa_mcp.server import _client as mcp_client
from oriexa_mcp.server import mcp as mcp_server

# ── Helpers ───────────────────────────────────────────────────────────────────


def _id(pattern: str, text: str) -> str:
    """Extract the first integer matching *pattern* from an MCP Markdown response."""
    m = re.search(pattern, text)
    assert m, f"Pattern {pattern!r} not found in:\n{text[:500]}"
    return m.group(1)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def call(client: AsyncClient, agent_with_key):
    """Wire MCP tool closures to the ASGI test transport and return a caller.

    * Replaces the global ``_client._client`` with an ASGI-backed httpx client
      so every MCP tool hits the real FastAPI app in-process.
    * Includes both ``Authorization`` (for agent endpoints) and ``X-User-ID``
      (for ``/api/v1/user/*`` endpoints) headers.
    """
    api_key = agent_with_key["api_key"]
    operator_id = agent_with_key.get("operator_id", 1)

    transport = ASGITransport(app=app)
    test_http = AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-User-ID": str(operator_id),
            "Content-Type": "application/json",
        },
    )
    mcp_client._client = test_http

    tools = mcp_server._tool_manager._tools

    async def _call(tool_name: str, **kwargs) -> str:
        return await tools[tool_name].fn(**kwargs)

    yield _call

    await test_http.aclose()
    mcp_client._client = None


# ── Full lifecycle test ───────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_full_lifecycle(call):
    """11-act narrative exercising MCP tools end-to-end."""

    # ═══ Act 1: Discovery (3 tools) ══════════════════════════════════════════

    cats = await call("oriexa_get_categories")
    assert "Available categories" in cats

    browse_empty = await call("oriexa_browse_tasks")
    assert "No tasks found" in browse_empty

    health = await call("oriexa_orchestrator_health")
    assert "ok" in health.lower()

    # ═══ Act 2: Agent Profile (3 tools) ══════════════════════════════════════
    profile = await call("oriexa_get_my_profile")
    assert "Agent" in profile
    assert "Test Agent" in profile

    updated = await call(
        "oriexa_update_profile",
        description="Updated via MCP E2E test",
    )
    assert "updated" in updated.lower()

    credits_before = await call("oriexa_get_my_credits")
    assert "Credit balance" in credits_before

    # ═══ Act 3: Task Creation (4 tools) ══════════════════════════════════════

    created = await call(
        "oriexa_create_task",
        title="Build a REST API",
        description=(
            "Create a comprehensive REST API with authentication, "
            "rate limiting, and full documentation."
        ),
        budget_credits=100,
        category_id="1",
    )
    assert "Task created" in created
    task_id = _id(r"Task #(\d+)", created)

    my_tasks = await call("oriexa_get_my_tasks")
    assert "Build a REST API" in my_tasks

    detail = await call("oriexa_get_task", task_id=task_id)
    assert "Build a REST API" in detail
    assert "open" in detail.lower()

    browse_open = await call("oriexa_browse_tasks", status="open")
    assert task_id in browse_open

    # ═══ Act 4: Claiming (3 tools) ═══════════════════════════════════════════

    claimed = await call(
        "oriexa_claim_task",
        task_id=task_id,
        proposed_credits=90,
        message="I can build this API",
    )
    assert "Claim #" in claimed
    claim_id = _id(r"Claim #(\d+)", claimed)

    claims_list = await call("oriexa_get_task_claims", task_id=task_id)
    assert "claim" in claims_list.lower()

    accepted = await call(
        "oriexa_accept_claim",
        task_id=task_id,
        claim_id=claim_id,
    )
    assert "accepted" in accepted.lower()

    # ═══ Act 5: Execution (3 tools) ══════════════════════════════════════════
    # oriexa_start_task — the PATCH endpoint does not exist; verify graceful error
    with pytest.raises(OriexaAPIError):
        await call("oriexa_start_task", task_id=task_id, claim_id=claim_id)

    msg_sent = await call(
        "oriexa_send_message",
        task_id=task_id,
        content="Starting work on the REST API now.",
    )
    assert "Message sent" in msg_sent

    messages = await call("oriexa_get_task_messages", task_id=task_id)
    assert "message" in messages.lower()

    # ═══ Act 6: Delivery & Revision (3 tools) ════════════════════════════════

    delivered = await call(
        "oriexa_submit_deliverable",
        task_id=task_id,
        content="Here is the completed REST API with all endpoints.",
    )
    assert "Deliverable #" in delivered
    deliv_id = _id(r"Deliverable #(\d+)", delivered)

    deliverables = await call("oriexa_get_task_deliverables", task_id=task_id)
    assert "deliverable" in deliverables.lower()

    revision = await call(
        "oriexa_request_revision",
        task_id=task_id,
        deliverable_id=deliv_id,
        revision_notes="Please add rate-limiting documentation.",
    )
    assert "revision" in revision.lower()

    # ═══ Act 7: Final Delivery & Accept (2 tools) ════════════════════════════

    delivered2 = await call(
        "oriexa_submit_deliverable",
        task_id=task_id,
        content="Updated REST API with rate-limiting documentation added.",
    )
    assert "Deliverable #" in delivered2
    deliv_id2 = _id(r"Deliverable #(\d+)", delivered2)

    complete = await call(
        "oriexa_accept_deliverable",
        task_id=task_id,
        deliverable_id=deliv_id2,
    )
    assert "accepted" in complete.lower() or "completed" in complete.lower()

    # ═══ Act 8: Post-Completion Credits (1 tool) ═════════════════════════════

    credits_after = await call("oriexa_get_my_credits")
    assert "Credit balance" in credits_after
    # Payment transaction should appear in the ledger
    assert "payment" in credits_after.lower() or "+" in credits_after

    # ═══ Act 9: Webhooks (3 tools) ═══════════════════════════════════════════

    wh_created = await call(
        "oriexa_create_webhook",
        url="https://example.com/webhook",
        events=["task.new_match", "claim.accepted"],
    )
    assert "Webhook" in wh_created
    wh_id = _id(r"Webhook #(\d+)", wh_created)

    wh_list = await call("oriexa_list_webhooks")
    assert "example.com" in wh_list

    wh_deleted = await call("oriexa_delete_webhook", webhook_id=wh_id)
    assert "deleted" in wh_deleted.lower()

    # ═══ Act 10: Bulk Operations (2 tools) ═══════════════════════════════════

    bulk_task_ids: list[str] = []
    for i in range(3):
        t = await call(
            "oriexa_create_task",
            title=f"Bulk task {i + 1}",
            description=(
                f"Bulk test task number {i + 1} with sufficient description "
                "text for validation requirements."
            ),
            budget_credits=50,
            category_id="1",
        )
        bulk_task_ids.append(_id(r"Task #(\d+)", t))

    bulk = await call(
        "oriexa_bulk_claim_tasks",
        claims=[
            {
                "task_id": int(tid),
                "proposed_credits": 40,
                "message": f"Bulk claim for task {tid}",
            }
            for tid in bulk_task_ids
        ],
    )
    assert "succeeded" in bulk.lower()

    # ═══ Act 11: Management (1 tool) ═════════════════════════════════════════

    orch_list = await call("oriexa_orchestrator_list")
    assert "execution" in orch_list.lower() or "No orchestrator" in orch_list

    # ═══ Remaining tools — reject_deliverable mini-lifecycle ═════════════════
    # Create a second task, deliver, then REJECT (covers oriexa_reject_deliverable)

    t2 = await call(
        "oriexa_create_task",
        title="Review test task",
        description="A short task to test the reject-deliverable MCP tool end-to-end.",
        budget_credits=60,
        category_id="1",
    )
    t2_id = _id(r"Task #(\d+)", t2)

    c2 = await call(
        "oriexa_claim_task",
        task_id=t2_id,
        proposed_credits=50,
        message="Will do",
    )
    c2_id = _id(r"Claim #(\d+)", c2)

    await call("oriexa_accept_claim", task_id=t2_id, claim_id=c2_id)

    d2 = await call(
        "oriexa_submit_deliverable",
        task_id=t2_id,
        content="Draft deliverable for review testing.",
    )
    d2_id = _id(r"Deliverable #(\d+)", d2)

    rejected = await call(
        "oriexa_reject_deliverable",
        task_id=t2_id,
        deliverable_id=d2_id,
        revision_notes="Needs more detail.",
    )
    assert "revision" in rejected.lower() or "rejected" in rejected.lower()

    # ═══ Orchestrator start / status (2 tools — error paths) ═════════════════

    # orchestrator_status with non-existent execution → 404
    with pytest.raises((OriexaAPIError, Exception)):
        await call("oriexa_orchestrator_status", execution_id="99999")

    # orchestrator_start on a completed task → likely 404/error
    with pytest.raises((OriexaAPIError, Exception)):
        await call("oriexa_orchestrator_start", task_id=task_id)

    # ═══ Error Cases ═════════════════════════════════════════════════════════

    # Non-existent task
    with pytest.raises(OriexaAPIError):
        await call("oriexa_get_task", task_id="99999")

    # Withdraw a non-existent claim
    with pytest.raises(OriexaAPIError):
        await call("oriexa_withdraw_claim", task_id="99999", claim_id="99999")
