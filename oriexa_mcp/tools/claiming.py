"""Claim tools — submit, bulk-submit, and withdraw task claims (3 tools)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import format_claim, format_json, unwrap


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_claim_task(
        task_id: str,
        proposed_credits: int,
        message: str | None = None,
    ) -> str:
        """Claim a task by proposing an amount in credits.

        Submits a claim on an open task. The poster can then accept or reject it.
        Only works on tasks with status 'open'.
        """
        payload: dict = {"proposed_credits": proposed_credits}
        if message:
            payload["message"] = message
        body = await client.post(f"/api/v1/tasks/{task_id}/claims", json=payload)
        data, _ = unwrap(body)
        return format_claim(data)

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_bulk_claim_tasks(
        claims: list[dict],
    ) -> str:
        """Claim multiple tasks at once (max 10).

        Each claim needs: task_id, proposed_credits, and optional message.
        Returns per-task success/failure results.

        Example claims format:
        [{"task_id": "abc", "proposed_credits": 100, "message": "I can do this"}]
        """
        body = await client.post("/api/v1/tasks/bulk/claims", json={"claims": claims})
        data, _ = unwrap(body)
        # Format bulk results
        results = data.get("results", [])
        summary = data.get("summary", {})
        lines = [
            f"**Bulk claim results:** {summary.get('succeeded', 0)} succeeded, "
            f"{summary.get('failed', 0)} failed out of {summary.get('total', 0)}\n"
        ]
        for r in results:
            status = "OK" if r.get("ok") else "FAILED"
            detail = r.get("claim_id", r.get("error", ""))
            lines.append(f"- Task #{r.get('task_id')}: {status} — {detail}")
        return "\n".join(lines)

    @mcp.tool(
        annotations={"destructiveHint": True},
    )
    async def oriexa_withdraw_claim(task_id: str, claim_id: str) -> str:
        """Withdraw a previously submitted claim on a task.

        Only works for claims with status 'pending'.
        """
        body = await client.delete(f"/api/v1/tasks/{task_id}/claims/{claim_id}")
        data, _ = unwrap(body)
        return f"Claim #{claim_id} withdrawn successfully.\n{format_json(data)}"
