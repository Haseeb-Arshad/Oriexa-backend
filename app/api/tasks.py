"""Orchestrator task management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.db.engine import async_session
from app.db.models import OrchTaskExecution

router = APIRouter(prefix="/orchestrator/tasks", tags=["orchestrator"])


@router.get("")
async def list_executions(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List all orchestrator task executions."""
    async with async_session() as session:
        query = select(OrchTaskExecution).order_by(OrchTaskExecution.id.desc())
        if status:
            query = query.where(OrchTaskExecution.status == status)
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        executions = result.scalars().all()

    return {
        "ok": True,
        "data": [
            {
                "id": ex.id,
                "oriexa_task_id": ex.oriexa_task_id,
                "status": ex.status,
                "graph_thread_id": ex.graph_thread_id,
                "total_tokens_used": ex.total_tokens_used,
                "attempt_count": ex.attempt_count,
                "error_message": ex.error_message,
                "started_at": ex.started_at.isoformat() if ex.started_at else None,
                "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
                "created_at": ex.created_at.isoformat(),
            }
            for ex in executions
        ],
    }


@router.get("/{execution_id}")
async def get_execution(execution_id: int) -> dict[str, Any]:
    """Get details of a specific execution."""
    async with async_session() as session:
        result = await session.execute(
            select(OrchTaskExecution).where(OrchTaskExecution.id == execution_id)
        )
        execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return {
        "ok": True,
        "data": {
            "id": execution.id,
            "oriexa_task_id": execution.oriexa_task_id,
            "status": execution.status,
            "task_snapshot": execution.task_snapshot,
            "graph_thread_id": execution.graph_thread_id,
            "workspace_path": execution.workspace_path,
            "total_tokens_used": execution.total_tokens_used,
            "total_cost_usd": execution.total_cost_usd,
            "error_message": execution.error_message,
            "attempt_count": execution.attempt_count,
            "claimed_credits": execution.claimed_credits,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            "created_at": execution.created_at.isoformat(),
        },
    }


@router.get("/by-task/{task_id}/active")
async def get_active_execution_for_task(task_id: int) -> dict[str, Any]:
    """Find the active (running) execution for a given Oriexa task ID."""
    from app.orchestrator.progress import progress_tracker

    async with async_session() as session:
        result = await session.execute(
            select(OrchTaskExecution)
            .where(
                OrchTaskExecution.oriexa_task_id == task_id,
                # Include completed so frontend can still display execution history
                OrchTaskExecution.status.in_([
                    "pending", "claiming", "clarifying", "planning",
                    "executing", "reviewing", "delivering", "completed",
                ]),
            )
            .order_by(OrchTaskExecution.id.desc())
            .limit(1)
        )
        execution = result.scalar_one_or_none()

    if not execution:
        return {"ok": True, "data": None}

    steps = progress_tracker.get_steps(execution.id)
    current_phase = steps[-1].phase if steps else None
    progress_pct = steps[-1].progress_pct if steps else 0

    return {
        "ok": True,
        "data": {
            "execution_id": execution.id,
            "status": execution.status,
            "current_phase": current_phase,
            "progress_pct": progress_pct,
        },
    }


@router.post("/{task_id}/start")
async def start_task(task_id: int) -> dict[str, Any]:
    """Manually trigger orchestration for a specific task ID."""
    # Import here to avoid circular imports at module level
    from app.orchestrator.task_picker import TaskPickerDaemon
    from app.orchestrator.concurrency import WorkerPool

    # Use the global daemon if available, otherwise create a temporary one
    pool = WorkerPool(max_concurrent=1)
    daemon = TaskPickerDaemon(worker_pool=pool)

    execution_id = await daemon.trigger_task(task_id)
    if execution_id is None:
        raise HTTPException(status_code=404, detail="Task not found or claim failed")

    return {
        "ok": True,
        "data": {
            "execution_id": execution_id,
            "oriexa_task_id": task_id,
            "status": "started",
            "message": f"Orchestration started for task {task_id}",
        },
    }
