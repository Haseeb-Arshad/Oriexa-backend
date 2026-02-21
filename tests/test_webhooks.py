"""Test webhook CRUD and limits."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_webhook(client: AsyncClient, auth_headers):
    resp = await client.post("/api/v1/webhooks", json={
        "url": "https://example.com/webhook",
        "events": ["task.new_match"],
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["url"] == "https://example.com/webhook"
    assert len(data["secret"]) == 64
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_list_webhooks(client: AsyncClient, auth_headers):
    await client.post("/api/v1/webhooks", json={
        "url": "https://example.com/wh1",
        "events": ["task.new_match"],
    }, headers=auth_headers)

    resp = await client.get("/api/v1/webhooks", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert "secret_prefix" in data[0]
    assert len(data[0]["secret_prefix"]) == 8


@pytest.mark.asyncio
async def test_delete_webhook(client: AsyncClient, auth_headers):
    create = await client.post("/api/v1/webhooks", json={
        "url": "https://example.com/wh-delete",
        "events": ["claim.accepted"],
    }, headers=auth_headers)
    wh_id = create.json()["data"]["id"]

    resp = await client.delete(f"/api/v1/webhooks/{wh_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True


@pytest.mark.asyncio
async def test_max_webhooks(client: AsyncClient, auth_headers):
    for i in range(5):
        resp = await client.post("/api/v1/webhooks", json={
            "url": f"https://example.com/wh{i}",
            "events": ["task.new_match"],
        }, headers=auth_headers)
        assert resp.status_code == 201

    resp = await client.post("/api/v1/webhooks", json={
        "url": "https://example.com/wh-overflow",
        "events": ["task.new_match"],
    }, headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "MAX_WEBHOOKS"
