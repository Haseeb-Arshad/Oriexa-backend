"""Orchestrator agent status endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.db.engine import async_session
from app.db.models import OrchAgentRun

router = APIRouter(prefix="/orchestrator/agents", tags=["orchestrator"])


@router.get("")
async def agent_status() -> dict[str, Any]:
    """Get status and stats for each agent role."""
    async with async_session() as session:
        result = await session.execute(
            select(
                OrchAgentRun.role,
                func.count(OrchAgentRun.id).label("total_runs"),
                func.sum(OrchAgentRun.prompt_tokens).label("total_prompt_tokens"),
                func.sum(OrchAgentRun.completion_tokens).label("total_completion_tokens"),
                func.avg(OrchAgentRun.duration_ms).label("avg_duration_ms"),
                func.sum(
                    func.cast(OrchAgentRun.success, type_=func.literal_column("int"))
                ).label("success_count"),
            ).group_by(OrchAgentRun.role)
        )
        rows = result.all()

    agents = []
    for row in rows:
        total = row.total_runs or 0
        success = row.success_count or 0
        agents.append({
            "role": row.role,
            "total_runs": total,
            "success_rate": round(success / total, 3) if total > 0 else 0.0,
            "total_prompt_tokens": row.total_prompt_tokens or 0,
            "total_completion_tokens": row.total_completion_tokens or 0,
            "avg_duration_ms": round(row.avg_duration_ms or 0),
        })

    return {"ok": True, "data": agents}
