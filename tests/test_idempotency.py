"""Test idempotency key handling."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_idempotency_replay(client: AsyncClient, auth_headers):
    body = {
        "title": "Idempotent Test Task Here",
        "description": "A task created with an idempotency key for replay testing",
        "budget_credits": 50,
    }
    headers = {**auth_headers, "Idempotency-Key": "test-key-123"}

    # First request
    resp1 = await client.post("/api/v1/tasks", json=body, headers=headers)
    assert resp1.status_code == 201

    # Second request with same key — should replay
    resp2 = await client.post("/api/v1/tasks", json=body, headers=headers)
    assert resp2.status_code == 201
    assert resp2.headers.get("X-Idempotency-Replayed") == "true"
    assert resp2.json()["data"]["id"] == resp1.json()["data"]["id"]


@pytest.mark.asyncio
async def test_idempotency_mismatch(client: AsyncClient, auth_headers):
    body1 = {
        "title": "First Task With Key",
        "description": "This task is the first one created with this key",
        "budget_credits": 50,
    }
    body2 = {
        "title": "Different Task Same Key",
        "description": "This task is different but uses the same idempotency key",
        "budget_credits": 75,
    }
    headers = {**auth_headers, "Idempotency-Key": "shared-key-456"}

    resp1 = await client.post("/api/v1/tasks", json=body1, headers=headers)
    assert resp1.status_code == 201

    resp2 = await client.post("/api/v1/tasks", json=body2, headers=headers)
    assert resp2.status_code == 422
    assert resp2.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"
