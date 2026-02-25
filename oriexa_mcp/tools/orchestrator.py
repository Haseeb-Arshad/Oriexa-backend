"""Orchestrator control & monitoring tools (4 tools)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_json, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_orchestrator_start(task_id: str) -> str:
        """Start the AI orchestrator on a specific task.

        Launches the multi-agent pipeline (Triage → Plan → Execute → Review → Deliver)
        to automatically work on and complete the task.
        """
        body = await client.post(f"/orchestrator/tasks/{task_id}/start")
        data, _ = unwrap(body)
        return f"Orchestrator started for task #{task_id}.\n{format_json(data)}"

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_orchestrator_status(execution_id: str) -> str:
        """Get the status of an orchestrator execution.

        Shows execution status, token usage, cost, workspace path,
        error messages, and timing information.
        """
        body = await client.get(f"/orchestrator/tasks/{execution_id}")
        data, _ = unwrap(body)
        if isinstance(data, dict):
            lines = [
                f"## Orchestrator Execution #{data.get('id', '?')}",
                "",
                f"**Task:** #{data.get('oriexa_task_id', '?')}  ",
                f"**Status:** {data.get('status', '?')}  ",
            ]
            if data.get("total_tokens_used"):
                lines.append(f"**Tokens used:** {data['total_tokens_used']}  ")
            if data.get("total_cost_usd"):
                lines.append(f"**Cost:** ${data['total_cost_usd']:.4f}  ")
            if data.get("error_message"):
                lines.append(f"**Error:** {data['error_message']}  ")
            if data.get("started_at"):
                lines.append(f"**Started:** {data['started_at']}  ")
            if data.get("completed_at"):
                lines.append(f"**Completed:** {data['completed_at']}  ")
            return "\n".join(lines)
        return format_json(data)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_orchestrator_list(
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> str:
        """List orchestrator task executions.

        Filter by status and paginate through results. Shows execution ID,
        task ID, status, and token usage.
        """
        params = {"status": status, "limit": limit, "offset": offset}
        body = await client.get("/orchestrator/tasks", params=params)
        data, _ = unwrap(body)
        if isinstance(data, list):
            if not data:
                return "No orchestrator executions found."
            lines = [f"**{len(data)}** execution(s):\n"]
            for ex in data:
                lines.append(
                    f"- **#{ex.get('id')}** task #{ex.get('oriexa_task_id', '?')} — "
                    f"status: {ex.get('status', '?')}, "
                    f"tokens: {ex.get('total_tokens_used', 0)}"
                )
            return "\n".join(lines)
        return format_json(data)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_orchestrator_health() -> str:
        """Check the health of the orchestrator service.

        Returns service status, metrics, and execution statistics.
        """
        body = await client.get("/orchestrator/health")
        lines = [
            "## Orchestrator Health",
            "",
            f"**Status:** {body.get('status', '?')}  ",
            f"**Service:** {body.get('service', '?')}  ",
        ]
        return "\n".join(lines)
