"""Test deliverable submission, acceptance, and revision requests."""

import pytest
from httpx import AsyncClient


async def _claim_task(client, auth_headers, task_id):
    """Helper: claim and accept a task, returning the claim ID."""
    claim = await client.post(
        f"/api/v1/tasks/{task_id}/claims",
        json={"proposed_credits": 80},
        headers=auth_headers,
    )
    claim_id = claim.json()["data"]["id"]
    await client.post(
        f"/api/v1/tasks/{task_id}/claims/accept",
        json={"claim_id": claim_id},
        headers=auth_headers,
    )
    return claim_id


@pytest.mark.asyncio
async def test_submit_deliverable(client: AsyncClient, auth_headers, open_task):
    await _claim_task(client, auth_headers, open_task["id"])

    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/deliverables",
        json={"content": "Here is my completed work for this task."},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["revision_number"] == 1
    assert data["status"] == "submitted"


@pytest.mark.asyncio
async def test_accept_deliverable_credits(client: AsyncClient, auth_headers, open_task):
    await _claim_task(client, auth_headers, open_task["id"])

    del_resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/deliverables",
        json={"content": "Completed deliverable content for acceptance testing"},
        headers=auth_headers,
    )
    deliverable_id = del_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/deliverables/accept",
        json={"deliverable_id": deliverable_id},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "completed"
    # Budget 100, 10% fee = 10, payment = 90
    assert data["credits_paid"] == 90
    assert data["platform_fee"] == 10


@pytest.mark.asyncio
async def test_request_revision(client: AsyncClient, auth_headers, open_task):
    await _claim_task(client, auth_headers, open_task["id"])

    del_resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/deliverables",
        json={"content": "First attempt at the deliverable work content"},
        headers=auth_headers,
    )
    deliverable_id = del_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/deliverables/revision",
        json={"deliverable_id": deliverable_id, "revision_notes": "Needs more detail"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "revision_requested"


@pytest.mark.asyncio
async def test_not_claimer_forbidden(client: AsyncClient, auth_headers, open_task):
    # Don't claim — try to deliver directly
    resp = await client.post(
        f"/api/v1/tasks/{open_task['id']}/deliverables",
        json={"content": "Should fail because task is not claimed yet."},
        headers=auth_headers,
    )
    # Task is open, not claimed/in_progress
    assert resp.status_code == 409
