"""Health and metrics endpoints for the orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

from app.db.engine import async_session
from app.db.models import OrchAgentRun, OrchTaskExecution

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "oriexa-orchestrator",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/metrics")
async def metrics():
    """Return orchestrator metrics."""
    async with async_session() as session:
        # Task execution counts by status
        status_counts = await session.execute(
            select(
                OrchTaskExecution.status,
                func.count(OrchTaskExecution.id),
            ).group_by(OrchTaskExecution.status)
        )
        executions_by_status = {row[0]: row[1] for row in status_counts.all()}

        # Total tokens used
        token_result = await session.execute(
            select(func.sum(OrchTaskExecution.total_tokens_used))
        )
        total_tokens = token_result.scalar() or 0

        # Agent run counts by role
        role_counts = await session.execute(
            select(
                OrchAgentRun.role,
                func.count(OrchAgentRun.id),
            ).group_by(OrchAgentRun.role)
        )
        runs_by_role = {row[0]: row[1] for row in role_counts.all()}

    return {
        "executions_by_status": executions_by_status,
        "total_tokens_used": total_tokens,
        "agent_runs_by_role": runs_by_role,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
