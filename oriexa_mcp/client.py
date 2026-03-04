"""Shared async HTTP client wrapper for the Oriexa REST API."""

from __future__ import annotations

from typing import Any

import httpx

from oriexa_mcp.config import settings
from oriexa_mcp.errors import parse_api_error


class OriexaClient:
    """Thin async wrapper around httpx that handles auth, base URL, and error translation."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        settings.validate()
        self._client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(settings.timeout),
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not started. Call start() first.")
        return self._client

    # ── Convenience methods ──────────────────────────────────────────

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        resp = await self.http.get(path, params=_strip_none(params))
        return self._handle(resp)

    async def post(self, path: str, json: dict[str, Any] | None = None) -> dict:
        resp = await self.http.post(path, json=json or {})
        return self._handle(resp)

    async def patch(self, path: str, json: dict[str, Any] | None = None) -> dict:
        resp = await self.http.patch(path, json=json or {})
        return self._handle(resp)

    async def delete(self, path: str) -> dict:
        resp = await self.http.delete(path)
        return self._handle(resp)

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _handle(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            raise parse_api_error(resp.status_code, body)
        return resp.json()


def _strip_none(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    return {k: v for k, v in params.items() if v is not None}
