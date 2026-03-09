"""Orchestrator task management endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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


@router.get("/{execution_id}/logs")
async def get_execution_logs(execution_id: int) -> dict[str, Any]:
    """Return raw execution logs for a task execution.

    Primary source is `.build_log` in the workspace.
    Fallback source is in-memory progress steps so the UI still shows
    useful live activity even when the log file is missing/empty.
    """
    from app.config import settings
    from app.orchestrator.progress import progress_tracker
    
    async with async_session() as session:
        result = await session.execute(
            select(OrchTaskExecution).where(OrchTaskExecution.id == execution_id)
        )
        execution = result.scalar_one_or_none()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    candidates: list[Path] = []
    if execution.workspace_path:
        candidates.append(Path(execution.workspace_path))
    # Fallbacks for orchestrator and legacy swarm workspace layouts.
    workspace_roots = {
        Path(settings.WORKSPACE_ROOT),
        Path(settings.AGENT_WORKSPACE_DIR),
        Path(__file__).resolve().parents[2] / "agent_works",
    }
    for root in workspace_roots:
        candidates.extend(
            [
                root / f"task-{execution_id}",
                root / f"task_{execution_id}",
                root / f"task-{execution.oriexa_task_id}",
                root / f"task_{execution.oriexa_task_id}",
            ]
        )

    seen: set[str] = set()
    workspace_candidates: list[Path] = []
    for base in candidates:
        key = str(base)
        if key in seen:
            continue
        seen.add(key)
        workspace_candidates.append(base)

    for workspace in workspace_candidates:
        log_file = workspace / ".build_log"
        if not log_file.exists() or not log_file.is_file():
            continue

        try:
            content = log_file.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                continue
            # Return last 12000 chars to preserve more context.
            if len(content) > 12000:
                content = "... (truncated) ...\n" + content[-12000:]
            return {"ok": True, "data": content}
        except Exception:
            # Try next candidate before falling back to progress steps.
            continue

    # Fallback 2: legacy/worker progress file when .build_log is missing.
    for workspace in workspace_candidates:
        progress_file = workspace / "progress.jsonl"
        if not progress_file.exists() or not progress_file.is_file():
            continue
        try:
            lines = [ln for ln in progress_file.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except Exception:
            continue
        if not lines:
            continue

        rendered: list[str] = []
        for raw in lines[-120:]:
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue

            ts_value = payload.get("timestamp")
            ts_text = "--:--:--"
            try:
                if isinstance(ts_value, (int, float)):
                    ts_text = datetime.fromtimestamp(float(ts_value), tz=timezone.utc).strftime("%H:%M:%S")
                elif isinstance(ts_value, str) and ts_value.strip():
                    dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                    ts_text = dt.astimezone(timezone.utc).strftime("%H:%M:%S")
            except Exception:
                pass

            phase = str(payload.get("phase") or "progress").lower()
            msg = str(payload.get("detail") or payload.get("description") or payload.get("title") or "").strip()
            if msg:
                rendered.append(f"[{ts_text}] [{phase}] {msg}")

        if rendered:
            return {"ok": True, "data": "\n".join(rendered)}

    # Fallback: synthesize logs from live progress steps.
    steps = progress_tracker.get_steps(execution_id)
    if steps:
        lines: list[str] = []
        for step in steps[-120:]:
            ts = datetime.fromtimestamp(step.timestamp, tz=timezone.utc).strftime("%H:%M:%S")
            msg = (step.detail or step.description or step.title or "").strip()
            if not msg:
                continue
            lines.append(f"[{ts}] [{step.phase}] {msg}")

        if lines:
            return {
                "ok": True,
                "data": "\n".join(lines),
            }

    return {"ok": True, "data": "Logs not available yet. Execution events will appear here once the agent starts writing output."}


@router.get("/by-task/{task_id}/active")
async def get_active_execution_for_task(task_id: int) -> dict[str, Any]:
    """Find the latest execution for a given Oriexa task ID.

    This intentionally includes failed executions so the frontend can render
    failure state instead of showing an indefinite startup loader.
    """
    from app.orchestrator.progress import progress_tracker

    async with async_session() as session:
        result = await session.execute(
            select(OrchTaskExecution)
            .where(OrchTaskExecution.oriexa_task_id == task_id)
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
