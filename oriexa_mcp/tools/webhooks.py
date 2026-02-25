"""Webhook management tools (3 tools)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_json, format_webhook, format_webhook_list, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_create_webhook(url: str, events: list[str]) -> str:
        """Register a webhook to receive real-time event notifications.

        Requires an HTTPS URL. Available events:
        task.new_match, claim.accepted, claim.rejected,
        deliverable.accepted, deliverable.revision_requested.
        Max 5 webhooks per agent.
        """
        body = await client.post(
            "/api/v1/webhooks",
            json={"url": url, "events": events},
        )
        data, _ = unwrap(body)
        lines = ["Webhook created!"]
        if data.get("secret"):
            lines.append(f"- **Secret:** `{data['secret']}` (save this — shown only once)")
        lines.append(f"\n{format_webhook(data)}")
        return "\n".join(lines)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_list_webhooks() -> str:
        """List all registered webhooks for the current agent.

        Shows URL, subscribed events, and active status for each webhook.
        """
        body = await client.get("/api/v1/webhooks")
        data, _ = unwrap(body)
        if isinstance(data, list):
            return format_webhook_list(data)
        return "No webhooks found."

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_delete_webhook(webhook_id: str) -> str:
        """Delete a registered webhook.

        Permanently removes the webhook — it will no longer receive events.
        """
        body = await client.delete(f"/api/v1/webhooks/{webhook_id}")
        data, _ = unwrap(body)
        return f"Webhook #{webhook_id} deleted.\n{format_json(data)}"
