"""Tests for the Oriexa API client."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.oriexa_client.client import OriexaClient


@pytest.fixture
def client():
    return OriexaClient(
        base_url="http://test-api:3000/api/v1",
        api_key="th_agent_" + "a" * 64,
    )


class TestOriexaClient:
    """Test the Oriexa API client methods."""

    @pytest.mark.asyncio
    async def test_browse_tasks_success(self, client):
        mock_response = httpx.Response(
            200,
            json={"ok": True, "data": [{"id": 1, "title": "Test Task"}]},
            request=httpx.Request("GET", "http://test"),
        )
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            tasks = await client.browse_tasks(status="open")
            assert len(tasks) == 1
            assert tasks[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_browse_tasks_empty(self, client):
        mock_response = httpx.Response(
            200,
            json={"ok": True, "data": []},
            request=httpx.Request("GET", "http://test"),
        )
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            tasks = await client.browse_tasks()
            assert tasks == []

    @pytest.mark.asyncio
    async def test_get_task_success(self, client):
        mock_response = httpx.Response(
            200,
            json={"ok": True, "data": {"id": 42, "title": "Test"}},
            request=httpx.Request("GET", "http://test"),
        )
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            task = await client.get_task(42)
            assert task["id"] == 42

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, client):
        mock_response = httpx.Response(
            404,
            json={"ok": False, "error": "Not found"},
            request=httpx.Request("GET", "http://test"),
        )
        mock_response.is_error = True
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(side_effect=httpx.HTTPStatusError(
                "Not found", request=mock_response.request, response=mock_response
            ))
            mock_get.return_value = mock_http

            result = await client.get_task(999)
            assert result is None

    @pytest.mark.asyncio
    async def test_claim_task(self, client):
        mock_response = httpx.Response(
            201,
            json={"ok": True, "data": {"id": 1, "status": "pending"}},
            request=httpx.Request("POST", "http://test"),
        )
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            result = await client.claim_task(1, 100, "I'll do it")
            assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_submit_deliverable(self, client):
        mock_response = httpx.Response(
            201,
            json={"ok": True, "data": {"id": 1, "content": "Done!"}},
            request=httpx.Request("POST", "http://test"),
        )
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http

            result = await client.submit_deliverable(1, "Done!")
            assert result is not None

    @pytest.mark.asyncio
    async def test_close(self, client):
        # Should not raise even if client was never opened
        await client.close()

    @pytest.mark.asyncio
    async def test_request_error_returns_none(self, client):
        with patch.object(client, "_get_client") as mock_get:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(
                side_effect=httpx.RequestError("Connection refused")
            )
            mock_get.return_value = mock_http

            result = await client.get_task(1)
            assert result is None
