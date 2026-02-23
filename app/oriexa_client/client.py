"""Async HTTP client for the Oriexa Next.js API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class OriexaClient:
    """Communicates with the Oriexa Next.js REST API using Bearer token auth."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or settings.ORIEXA_API_BASE_URL).rstrip("/")
        self.api_key = api_key or settings.ORIEXA_API_KEY
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # -- helpers --

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        client = await self._get_client()
        try:
            resp = await client.request(method, path, **kwargs)
            resp.raise_for_status()
            body = resp.json()
            if isinstance(body, dict) and "data" in body:
                return body["data"]
            return body
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Oriexa API %s %s -> %s: %s",
                method, path, exc.response.status_code, exc.response.text[:500],
            )
            return None
        except httpx.RequestError as exc:
            logger.error("Oriexa API request failed: %s", exc)
            return None

    # -- Task browsing --

    async def browse_tasks(
        self,
        status: str = "open",
        category: str | None = None,
        limit: int = 20,
        sort: str = "newest",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"status": status, "limit": limit, "sort": sort}
        if category:
            params["category"] = category
        result = await self._request("GET", "/tasks", params=params)
        if isinstance(result, list):
            return result
        return result.get("items", []) if isinstance(result, dict) else []

    async def get_task(self, task_id: int) -> dict[str, Any] | None:
        return await self._request("GET", f"/tasks/{task_id}")

    # -- Claims --

    async def claim_task(
        self,
        task_id: int,
        proposed_credits: int,
        message: str | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"proposedCredits": proposed_credits}
        if message:
            payload["message"] = message
        return await self._request("POST", f"/tasks/{task_id}/claims", json=payload)

    # -- Deliverables --

    async def submit_deliverable(
        self, task_id: int, content: str
    ) -> dict[str, Any] | None:
        return await self._request(
            "POST", f"/tasks/{task_id}/deliverables", json={"content": content}
        )

    async def get_deliverables(self, task_id: int) -> list[dict[str, Any]]:
        result = await self._request("GET", f"/tasks/{task_id}/deliverables")
        if isinstance(result, list):
            return result
        return result.get("items", []) if isinstance(result, dict) else []

    # -- Agent profile --

    async def get_agent_profile(self) -> dict[str, Any] | None:
        return await self._request("GET", "/agents/me")

    async def get_agent_credits(self) -> dict[str, Any] | None:
        return await self._request("GET", "/agents/me/credits")

    # -- Webhooks --

    async def register_webhook(
        self,
        url: str,
        events: list[str],
    ) -> dict[str, Any] | None:
        """Register a webhook endpoint for the agent."""
        return await self._request(
            "POST", "/webhooks",
            json={"url": url, "events": events},
        )

    async def list_webhooks(self) -> list[dict[str, Any]]:
        """List all registered webhooks for this agent."""
        result = await self._request("GET", "/webhooks")
        if isinstance(result, list):
            return result
        return result.get("items", []) if isinstance(result, dict) else []

    async def delete_webhook(self, webhook_id: int) -> dict[str, Any] | None:
        """Delete a registered webhook."""
        return await self._request("DELETE", f"/webhooks/{webhook_id}")

    # -- Agent profile update --

    async def update_agent_profile(
        self, **fields: Any
    ) -> dict[str, Any] | None:
        """Update agent profile fields (e.g. webhook_url)."""
        return await self._request("PATCH", "/agents/me", json=fields)
