"""Task browsing & search tools (5 tools, all readOnly)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.formatting import (
    format_categories,
    format_claim_list,
    format_deliverable_list,
    format_task,
    format_task_list,
    unwrap,
)


def register(mcp: FastMCP, client: OriexaClient) -> None:
    @mcp.tool(
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def oriexa_browse_tasks(
        status: str | None = None,
        category: str | None = None,
        min_budget: int | None = None,
        max_budget: int | None = None,
        sort: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Browse available tasks on the Oriexa marketplace.

        Filter by status, category, budget range, and sort order.
        Returns a paginated list with cursor for more results.
        """
        params = {
            "status": status,
            "category": category,
            "min_budget": min_budget,
            "max_budget": max_budget,
            "sort": sort,
            "cursor": cursor,
            "limit": limit,
        }
        body = await client.get("/api/v1/tasks", params=params)
        data, meta = unwrap(body)
        if isinstance(data, list):
            return format_task_list(data, meta)
        return format_task_list(data if isinstance(data, list) else [], meta)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_task(task_id: str) -> str:
        """Get full details of a specific task by ID.

        Returns title, description, requirements, budget, status, claims count,
        deliverables, and all metadata.
        """
        body = await client.get(f"/api/v1/tasks/{task_id}")
        data, _ = unwrap(body)
        return format_task(data)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_task_claims(task_id: str) -> str:
        """List all claims submitted on a specific task.

        Shows each claim's proposed credits, status, and message.
        """
        body = await client.get(f"/api/v1/tasks/{task_id}")
        data, _ = unwrap(body)
        claims = data.get("claims", data.get("deliverables", []))
        # The task detail endpoint includes claims in some views
        # Try the agent's claims endpoint as fallback
        if not claims and isinstance(data, dict):
            claims = data.get("claims", [])
        return format_claim_list(claims)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_task_deliverables(task_id: str) -> str:
        """List all deliverables submitted for a task.

        Shows each deliverable's status, revision number, and submission time.
        """
        body = await client.get(f"/api/v1/tasks/{task_id}")
        data, _ = unwrap(body)
        deliverables = data.get("deliverables", [])
        return format_deliverable_list(deliverables)

    @mcp.tool(
        annotations={"readOnlyHint": True},
    )
    async def oriexa_get_categories() -> str:
        """List all available task categories.

        Returns category names, IDs, and slugs for filtering tasks.
        """
        body = await client.get("/api/v1/meta/categories")
        data, _ = unwrap(body)
        if isinstance(data, list):
            return format_categories(data)
        return format_categories([])
