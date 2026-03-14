from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import async_test_session_factory


async def _bootstrap(
    client: AsyncClient,
    *,
    email: str,
    scope: str,
    name: str,
) -> dict:
    response = await client.post(
        "/api/v2/external/sessions/bootstrap",
        json={
            "email": email,
            "password": "password123",
            "name": name,
            "scope": scope,
            "agent_name": f"{name} Agent",
            "capabilities": ["coding"],
            "category_ids": [1],
        },
    )
    assert response.status_code == 201
    return response.json()["data"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_fresh_schema_contains_role_and_orchestrator_tables():
    async with async_test_session_factory() as session:
        role_result = await session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'role'
                """
            )
        )
        assert role_result.scalar_one_or_none() == "role"

        orch_result = await session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name IN ('orch_task_executions', 'orch_subtasks', 'orch_messages', 'orch_agent_runs')
                ORDER BY table_name
                """
            )
        )
        assert [row.table_name for row in orch_result.all()] == [
            "orch_agent_runs",
            "orch_messages",
            "orch_subtasks",
            "orch_task_executions",
        ]


@pytest.mark.asyncio
async def test_external_v2_revision_loop(client: AsyncClient):
    poster = await _bootstrap(
        client,
        email="poster-v2@example.com",
        scope="poster",
        name="Poster V2",
    )
    worker = await _bootstrap(
        client,
        email="worker-v2@example.com",
        scope="worker",
        name="Worker V2",
    )

    created = await client.post(
        "/api/v2/external/tasks",
        json={
            "title": "Unify the outside-agent flow",
            "description": "Build a clean end-to-end lifecycle for outside agents using a single token and a single contract.",
            "budget_credits": 150,
            "category_id": 1,
            "requirements": "The workflow object must always show the next valid step.",
            "max_revisions": 2,
        },
        headers=_auth(poster["token"]),
    )
    assert created.status_code == 201
    task = created.json()["data"]
    task_id = task["id"]

    marketplace = await client.get(
        "/api/v2/external/tasks",
        params={"view": "marketplace"},
        headers=_auth(worker["token"]),
    )
    assert marketplace.status_code == 200
    assert any(item["id"] == task_id for item in marketplace.json()["data"]["items"])

    claimed = await client.post(
        f"/api/v2/external/tasks/{task_id}/claim",
        json={"proposed_credits": 120, "message": "I can do this through v2."},
        headers=_auth(worker["token"]),
    )
    assert claimed.status_code == 201
    claim_task = claimed.json()["data"]
    claim_id = claim_task["claims"][0]["id"]

    poster_view = await client.get(
        f"/api/v2/external/tasks/{task_id}",
        headers=_auth(poster["token"]),
    )
    assert poster_view.status_code == 200
    assert poster_view.json()["data"]["workflow"]["phase"] == "awaiting_claim_acceptance"
    assert "accept_claim" in poster_view.json()["data"]["workflow"]["next_actions"]

    accepted = await client.post(
        f"/api/v2/external/tasks/{task_id}/accept-claim",
        json={"claim_id": claim_id},
        headers=_auth(poster["token"]),
    )
    assert accepted.status_code == 200

    worker_state = await client.get(
        f"/api/v2/external/tasks/{task_id}/state",
        headers=_auth(worker["token"]),
    )
    assert worker_state.status_code == 200
    assert "submit_deliverable" in worker_state.json()["data"]["workflow"]["next_actions"]

    asked = await client.post(
        f"/api/v2/external/tasks/{task_id}/messages",
        json={
            "content": "Should the final handoff include MCP setup notes?",
            "message_type": "question",
            "structured_data": {
                "question_id": "handoff",
                "question_type": "yes_no",
                "options": ["Yes", "No"],
            },
        },
        headers=_auth(worker["token"]),
    )
    assert asked.status_code == 201
    question_messages = [m for m in asked.json()["data"]["messages"] if m["message_type"] == "question"]
    assert question_messages
    question_id = question_messages[-1]["id"]

    answered = await client.patch(
        f"/api/v2/external/tasks/{task_id}/questions/{question_id}",
        json={"response": "Yes, include the MCP handoff.", "option_index": 0},
        headers=_auth(poster["token"]),
    )
    assert answered.status_code == 200

    delivered_once = await client.post(
        f"/api/v2/external/tasks/{task_id}/deliverables",
        json={"content": "First deliverable draft."},
        headers=_auth(worker["token"]),
    )
    assert delivered_once.status_code == 201
    first_deliverable_id = delivered_once.json()["data"]["deliverables"][0]["id"]

    poster_delivered_state = await client.get(
        f"/api/v2/external/tasks/{task_id}/state",
        headers=_auth(poster["token"]),
    )
    assert poster_delivered_state.status_code == 200
    assert "accept_deliverable" in poster_delivered_state.json()["data"]["workflow"]["next_actions"]
    assert "request_revision" in poster_delivered_state.json()["data"]["workflow"]["next_actions"]

    revised = await client.post(
        f"/api/v2/external/tasks/{task_id}/request-revision",
        json={"deliverable_id": first_deliverable_id, "notes": "Add a clearer summary of the token flow."},
        headers=_auth(poster["token"]),
    )
    assert revised.status_code == 200
    assert revised.json()["data"]["workflow"]["phase"] == "revision_requested"

    delivered_twice = await client.post(
        f"/api/v2/external/tasks/{task_id}/deliverables",
        json={"content": "Final deliverable with the token flow clarified."},
        headers=_auth(worker["token"]),
    )
    assert delivered_twice.status_code == 201
    final_deliverable_id = delivered_twice.json()["data"]["deliverables"][0]["id"]

    completed = await client.post(
        f"/api/v2/external/tasks/{task_id}/accept-deliverable",
        json={"deliverable_id": final_deliverable_id},
        headers=_auth(poster["token"]),
    )
    assert completed.status_code == 200
    assert completed.json()["data"]["status"] == "completed"
    assert completed.json()["data"]["workflow"]["phase"] == "completed"


@pytest.mark.asyncio
async def test_external_v2_hybrid_and_webhook_crud(client: AsyncClient):
    hybrid = await _bootstrap(
        client,
        email="hybrid-v2@example.com",
        scope="hybrid",
        name="Hybrid V2",
    )

    created = await client.post(
        "/api/v2/external/webhooks",
        json={
            "url": "https://example.com/oriexa",
            "events": ["task.updated", "claim.created", "deliverable.submitted", "message.created"],
        },
        headers=_auth(hybrid["token"]),
    )
    assert created.status_code == 201
    webhook_id = created.json()["data"]["id"]
    assert created.json()["data"]["secret"].startswith(created.json()["data"]["secret_prefix"])

    listed = await client.get(
        "/api/v2/external/webhooks",
        headers=_auth(hybrid["token"]),
    )
    assert listed.status_code == 200
    assert any(item["id"] == webhook_id for item in listed.json()["data"])

    deleted = await client.delete(
        f"/api/v2/external/webhooks/{webhook_id}",
        headers=_auth(hybrid["token"]),
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
