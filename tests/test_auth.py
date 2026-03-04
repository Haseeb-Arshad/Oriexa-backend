"""Test auth registration."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    resp = await client.post("/api/auth/register", json={
        "email": "new@example.com",
        "password": "password123",
        "name": "New User",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] > 0
    assert data["email"] == "new@example.com"
    assert data["name"] == "New User"


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient, registered_user):
    resp = await client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "password123",
        "name": "Another User",
    })
    assert resp.status_code == 409
    assert "already exists" in resp.json()["error"]


@pytest.mark.asyncio
async def test_register_validation_error(client: AsyncClient):
    resp = await client.post("/api/auth/register", json={
        "email": "bad",
        "password": "pw",
        "name": "",
    })
    assert resp.status_code == 400
