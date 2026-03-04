"""Test rate limiting: 100 req/min, headers, 429 on 101st."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_rate_limit_headers_present(client: AsyncClient, auth_headers):
    resp = await client.get("/api/v1/tasks", headers=auth_headers)
    assert resp.status_code == 200
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers
    assert "X-RateLimit-Reset" in resp.headers
    assert resp.headers["X-RateLimit-Limit"] == "100"


@pytest.mark.asyncio
async def test_rate_limit_429(client: AsyncClient, auth_headers):
    # Fire 100 requests (should all succeed)
    for _ in range(100):
        resp = await client.get("/api/v1/tasks", headers=auth_headers)
        assert resp.status_code == 200

    # 101st request should be rate limited
    resp = await client.get("/api/v1/tasks", headers=auth_headers)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"
    assert "X-RateLimit-Remaining" in resp.headers
    assert resp.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_auth_errors_no_rate_limit_headers(client: AsyncClient):
    # No auth header
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 401
    assert "X-RateLimit-Limit" not in resp.headers

    # Bad auth header
    resp = await client.get("/api/v1/tasks", headers={"Authorization": "Bearer badtoken"})
    assert resp.status_code == 401
    assert "X-RateLimit-Limit" not in resp.headers
