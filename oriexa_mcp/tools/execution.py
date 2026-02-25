"""Task lifecycle tools — start work, view messages (2 tools)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_json, format_messages, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_start_task(task_id: str, claim_id: str) -> str:
        """Start working on a claimed task.

        Transitions the task from 'claimed' to 'in_progress'. You must have
        an accepted claim on this task first.
        """
        body = await client.patch(
            f"/api/v1/tasks/{task_id}/claims/{claim_id}/start"
        )
        data, _ = unwrap(body)
        return f"Task #{task_id} started!\n{format_json(data)}"

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_task_messages(task_id: str) -> str:
        """Get all messages/conversation for a task.

        Returns the full message thread between poster and agent,
        including clarification questions, status updates, and feedback.
        """
        body = await client.get(f"/api/v1/tasks/{task_id}/messages")
        data, _ = unwrap(body)
        if isinstance(data, list):
            return format_messages(data)
        # Some endpoints return {messages: [...]}
        messages = data.get("messages", []) if isinstance(data, dict) else []
        return format_messages(messages)
