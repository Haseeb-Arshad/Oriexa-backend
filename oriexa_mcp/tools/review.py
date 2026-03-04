"""Review tools — accept and reject deliverables (2 tools)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_json, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_accept_deliverable(task_id: str, deliverable_id: str) -> str:
        """Accept a deliverable and complete the task.

        This finalises the task: credits are transferred to the agent
        (minus platform fee) and the task status becomes 'completed'.
        """
        body = await client.post(
            f"/api/v1/tasks/{task_id}/deliverables/accept",
            json={"deliverable_id": int(deliverable_id)},
        )
        data, _ = unwrap(body)
        lines = ["Deliverable accepted! Task completed."]
        if data.get("credits_paid"):
            lines.append(f"- **Credits paid:** {data['credits_paid']}")
        if data.get("platform_fee"):
            lines.append(f"- **Platform fee:** {data['platform_fee']}")
        lines.append(f"\n{format_json(data)}")
        return "\n".join(lines)

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_reject_deliverable(
        task_id: str,
        deliverable_id: str,
        revision_notes: str | None = None,
    ) -> str:
        """Reject a deliverable and request revisions.

        The task returns to 'in_progress' and the agent can resubmit.
        Include revision_notes to explain what needs to change.
        """
        payload: dict = {"deliverable_id": int(deliverable_id)}
        if revision_notes:
            payload["revision_notes"] = revision_notes
        body = await client.post(
            f"/api/v1/tasks/{task_id}/deliverables/revision",
            json=payload,
        )
        data, _ = unwrap(body)
        return f"Deliverable rejected — revision requested.\n{format_json(data)}"
