"""Test agent registration, profile, and update."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_agent(client: AsyncClient, registered_user):
    resp = await client.post("/api/v1/agents", json={
        "email": "test@example.com",
        "password": "password123",
        "name": "My Agent",
        "description": "A capable agent for all sorts of tasks",
        "capabilities": ["coding", "research"],
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    if "data" in data:
        data = data["data"]
    assert data["api_key"].startswith("th_agent_")
    assert len(data["api_key"]) == 72


@pytest.mark.asyncio
async def test_register_bad_credentials(client: AsyncClient, registered_user):
    resp = await client.post("/api/v1/agents", json={
        "email": "test@example.com",
        "password": "wrongpass",
        "name": "My Agent",
        "description": "A capable agent for tasks and work",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_profile(client: AsyncClient, auth_headers):
    resp = await client.get("/api/v1/agents/me", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "id" in data
    assert "operator" in data
    assert data["operator"]["credit_balance"] >= 0


@pytest.mark.asyncio
async def test_update_profile(client: AsyncClient, auth_headers):
    resp = await client.patch("/api/v1/agents/me", json={
        "name": "Updated Agent Name",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "Updated Agent Name"


@pytest.mark.asyncio
async def test_public_agent_view(client: AsyncClient, auth_headers, agent_with_key):
    agent_id = agent_with_key["agent_id"]
    resp = await client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == agent_id
    # No sensitive data
    assert "api_key" not in data
    assert "api_key_hash" not in data
