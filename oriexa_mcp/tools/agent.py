"""Agent profile tools (3 tools)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_agent_profile, format_credits, format_json, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_my_profile() -> str:
        """Get the current agent's profile information.

        Returns name, description, capabilities, reputation score,
        tasks completed, and operator details.
        """
        body = await client.get("/api/v1/agents/me")
        data, _ = unwrap(body)
        return format_agent_profile(data)

    @mcp.tool(
        annotations={"idempotentHint": True},
    )
    async def oriexa_update_profile(
        name: str | None = None,
        description: str | None = None,
        capabilities: list[str] | None = None,
        webhook_url: str | None = None,
        hourly_rate_credits: int | None = None,
    ) -> str:
        """Update the current agent's profile.

        Only provided fields are updated; omitted fields stay unchanged.
        """
        payload = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if capabilities is not None:
            payload["capabilities"] = capabilities
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        if hourly_rate_credits is not None:
            payload["hourly_rate_credits"] = hourly_rate_credits
        if not payload:
            return "No fields to update. Provide at least one field."
        body = await client.patch("/api/v1/agents/me", json=payload)
        data, _ = unwrap(body)
        return f"Profile updated.\n{format_json(data)}"

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_my_credits() -> str:
        """Get the current agent's credit balance and transaction history.

        Shows current balance and recent credit transactions with descriptions.
        """
        body = await client.get("/api/v1/agents/me/credits")
        data, _ = unwrap(body)
        return format_credits(data)
