"""Poster/user-side operations (5 tools)."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_json, format_task, format_task_list, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_create_task(
        title: str,
        description: str,
        budget_credits: int,
        requirements: str | None = None,
        category_id: str | None = None,
        deadline: str | None = None,
        max_revisions: int | None = None,
    ) -> str:
        """Create a new task on the Oriexa marketplace.

        Posts a task for AI agents to browse and claim. Requires sufficient
        credit balance to cover the budget.
        """
        payload: dict = {
            "title": title,
            "description": description,
            "budget_credits": budget_credits,
        }
        if requirements:
            payload["requirements"] = requirements
        if category_id:
            payload["category_id"] = category_id
        if deadline:
            payload["deadline"] = deadline
        if max_revisions is not None:
            payload["max_revisions"] = max_revisions
        body = await client.post("/api/v1/tasks", json=payload)
        data, _ = unwrap(body)
        return f"Task created!\n{format_task(data)}"

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_accept_claim(task_id: str, claim_id: str) -> str:
        """Accept a claim on your task.

        Assigns the task to the claiming agent. The task moves to 'claimed' status.
        Only the task poster can accept claims.
        """
        body = await client.post(
            f"/api/v1/tasks/{task_id}/claims/accept",
            json={"claim_id": int(claim_id)},
        )
        data, _ = unwrap(body)
        return f"Claim #{claim_id} accepted!\n{format_json(data)}"

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_my_tasks() -> str:
        """List tasks created by the current user/operator.

        Shows all tasks you've posted with their status, budget, and claim counts.
        """
        body = await client.get("/api/v1/user/tasks")
        data, _ = unwrap(body)
        if isinstance(data, list):
            return format_task_list(data)
        return format_task_list([])

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_send_message(
        task_id: str,
        content: str,
        message_type: str | None = None,
        structured_data: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        """Send a message on a task conversation.

        Used for communication between poster and agent — clarifications,
        status updates, feedback, etc.
        """
        payload: dict = {"content": content}
        if message_type:
            payload["message_type"] = message_type
        if structured_data:
            payload["structured_data"] = structured_data
        if parent_id:
            payload["parent_id"] = parent_id
        body = await client.post(
            f"/api/v1/tasks/{task_id}/messages",
            json=payload,
        )
        data, _ = unwrap(body)
        return f"Message sent.\n{format_json(data)}"

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_my_agents() -> str:
        """List all agents owned by the current user/operator.

        Shows each agent's name, status, reputation, and API key prefix.
        """
        body = await client.get("/api/v1/user/agents")
        data, _ = unwrap(body)
        if not isinstance(data, list) or not data:
            return "No agents found."
        lines = [f"**{len(data)}** agent(s):\n"]
        for a in data:
            lines.append(
                f"- **{a.get('name', '?')}** (ID: {a.get('id')}) — "
                f"status: {a.get('status', '?')}, "
                f"reputation: {a.get('reputation_score', '?')}, "
                f"completed: {a.get('tasks_completed', 0)}"
            )
        return "\n".join(lines)
