"""Test claim creation, duplication, acceptance, and bulk claims."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_claim(client: AsyncClient, auth_headers, open_task):
    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/claims",
        json={"proposed_credits": 80, "message": "I can do this"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["proposed_credits"] == 80
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_duplicate_claim(client: AsyncClient, auth_headers, open_task):
    await client.post(
        f"/api/v1/tasks/{open_task['id']}/claims",
        json={"proposed_credits": 80},
        headers=auth_headers,
    )
    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/claims",
        json={"proposed_credits": 80},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_CLAIM"


@pytest.mark.asyncio
async def test_credits_exceed_budget(client: AsyncClient, auth_headers, open_task):
    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/claims",
        json={"proposed_credits": 999},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_CREDITS"


@pytest.mark.asyncio
async def test_accept_claim(client: AsyncClient, auth_headers, open_task):
    claim_resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/claims",
        json={"proposed_credits": 80},
        headers=auth_headers,
    )
    claim_id = claim_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/claims/accept",
        json={"claim_id": claim_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_bulk_claims(client: AsyncClient, auth_headers):
    # Create multiple tasks
    task_ids = []
    for i in range(3):
        resp = await client.post("/api/v1/tasks", json={
            "title": f"Bulk Test Task {i}",
            "description": "A task created for bulk claims testing purposes here",
            "budget_credits": 50,
        }, headers=auth_headers)
        task_ids.append(resp.json()["data"]["id"])

    resp = await client.post(
        "/api/v1/tasks/bulk/claims",
        json={
            "claims": [
                {"task_id": task_ids[0], "proposed_credits": 40},
                {"task_id": task_ids[1], "proposed_credits": 45},
                {"task_id": 99999, "proposed_credits": 10},  # non-existent
            ]
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"]["succeeded"] == 2
    assert data["summary"]["failed"] == 1
    assert data["summary"]["total"] == 3
