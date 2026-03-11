"""Test agent endpoint decommission + remaining profile operations."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_public_agent_registration_endpoint_removed(client: AsyncClient, registered_user):
    resp = await client.post("/api/v1/agents", json={
        "email": "test@example.com",
        "password": "password123",
        "name": "My Agent",
        "description": "A capable agent for all sorts of tasks",
        "capabilities": ["coding", "research"],
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_agent_management_endpoints_removed(client: AsyncClient, registered_user):
    headers = {"X-User-ID": str(registered_user["id"])}
    get_resp = await client.get("/api/v1/user/agents", headers=headers)
    post_resp = await client.post("/api/v1/user/agents", json={"name": "A"}, headers=headers)
    regen_resp = await client.post("/api/v1/user/agents/1/regenerate-key", headers=headers)
    revoke_resp = await client.post("/api/v1/user/agents/1/revoke-key", headers=headers)

    assert get_resp.status_code == 404
    assert post_resp.status_code == 404
    assert regen_resp.status_code == 404
    assert revoke_resp.status_code == 404


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
