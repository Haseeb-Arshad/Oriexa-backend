"""Test task browsing and creation."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_browse_tasks_empty(client: AsyncClient, auth_headers):
    resp = await client.get("/api/v1/tasks", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"] == []
    assert body["meta"]["has_more"] is False


@pytest.mark.asyncio
async def test_create_task(client: AsyncClient, auth_headers):
    resp = await client.post("/api/v1/tasks", json={
        "title": "Build a REST API",
        "description": "Create a comprehensive REST API with authentication and rate limiting",
        "budget_credits": 100,
        "category_id": 1,
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["title"] == "Build a REST API"
    assert data["budget_credits"] == 100
    assert data["status"] == "open"


@pytest.mark.asyncio
async def test_browse_with_filters(client: AsyncClient, auth_headers, open_task):
    resp = await client.get("/api/v1/tasks?status=open&category=1", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) >= 1


@pytest.mark.asyncio
async def test_task_detail(client: AsyncClient, auth_headers, open_task):
    task_id = open_task["id"]
    resp = await client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == task_id
    assert data["claims_count"] == 0


@pytest.mark.asyncio
async def test_task_not_found(client: AsyncClient, auth_headers):
    resp = await client.get("/api/v1/tasks/99999", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_browse_pagination(client: AsyncClient, auth_headers):
    # Create 3 tasks
    for i in range(3):
        await client.post("/api/v1/tasks", json={
            "title": f"Pagination Test Task {i}",
            "description": "A task with enough description for testing pagination features",
            "budget_credits": 50 + i * 10,
        }, headers=auth_headers)

    resp = await client.get("/api/v1/tasks?limit=2", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    assert body["meta"]["has_more"] is True
    assert body["meta"]["cursor"] is not None

    # Use cursor
    resp2 = await client.get(f"/api/v1/tasks?limit=2&cursor={body['meta']['cursor']}", headers=auth_headers)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["data"]) >= 1


@pytest.mark.asyncio
async def test_browse_sort_budget_high(client: AsyncClient, auth_headers):
    for budget in [50, 100, 75]:
        await client.post("/api/v1/tasks", json={
            "title": f"Budget {budget} task here",
            "description": "A task with specific budget for testing sort functionality",
            "budget_credits": budget,
        }, headers=auth_headers)

    resp = await client.get("/api/v1/tasks?sort=budget_high", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    budgets = [t["budget_credits"] for t in data]
    assert budgets == sorted(budgets, reverse=True)
