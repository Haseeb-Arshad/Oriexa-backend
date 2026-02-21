"""Test credit system: welcome bonus, agent bonus, task completion."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_welcome_bonus(client: AsyncClient, auth_headers):
    """Registering gives 500 welcome credits."""
    resp = await client.get("/api/v1/agents/me/credits", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Should have welcome bonus (500) + agent bonus (100)
    assert data["credit_balance"] == 600
    assert len(data["transactions"]) >= 2  # welcome + agent bonus


@pytest.mark.asyncio
async def test_task_completion_credits(client: AsyncClient, auth_headers, open_task):
    """Completing a task awards budget - 10% fee."""
    task_id = open_task["id"]

    # Claim
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

    # Deliver
    del_resp = await client.post(
        f"/api/v1/tasks/{task_id}/deliverables",
        json={"content": "Completed work for task completion credit verification"},
        headers=auth_headers,
    )
    del_id = del_resp.json()["data"]["id"]

    # Accept
    accept = await client.post(
        f"/api/v1/tasks/{task_id}/deliverables/accept",
        json={"deliverable_id": del_id},
        headers=auth_headers,
    )
    assert accept.status_code == 200
    data = accept.json()["data"]
    # Budget=100, fee=10, payment=90
    assert data["credits_paid"] == 90
    assert data["platform_fee"] == 10


@pytest.mark.asyncio
async def test_floor_division():
    """Verify platform fee uses integer floor division."""
    from app.constants import PLATFORM_FEE_PERCENT
    budget = 15
    fee = budget * PLATFORM_FEE_PERCENT // 100
    payment = budget - fee
    assert fee == 1
    assert payment == 14
