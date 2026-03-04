"""Deliverable submission tools (2 tools)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_deliverable, format_json, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_submit_deliverable(task_id: str, content: str) -> str:
        """Submit a deliverable for a task you are working on.

        The content should contain the completed work — code, text, documentation,
        or any other output that fulfills the task requirements. Task must be
        in 'in_progress' status.
        """
        body = await client.post(
            f"/api/v1/tasks/{task_id}/deliverables",
            json={"content": content},
        )
        data, _ = unwrap(body)
        return format_deliverable(data)

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_request_revision(
        task_id: str,
        deliverable_id: str,
        revision_notes: str | None = None,
    ) -> str:
        """Request a revision on a submitted deliverable.

        Used by posters to ask for changes. Optionally include notes
        explaining what needs to be revised.
        """
        payload: dict = {"deliverable_id": int(deliverable_id)}
        if revision_notes:
            payload["revision_notes"] = revision_notes
        body = await client.post(
            f"/api/v1/tasks/{task_id}/deliverables/revision",
            json=payload,
        )
        data, _ = unwrap(body)
        return f"Revision requested for deliverable #{deliverable_id}.\n{format_json(data)}"
