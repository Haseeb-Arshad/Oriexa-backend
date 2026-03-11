from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import OrchTaskStatus
from app.db.models import OrchTaskExecution


def legacy_workspace_candidates(
    task_id: int,
    execution_id: int | None = None,
    workspace_path: str | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    if workspace_path:
        candidates.append(Path(workspace_path))

    roots = [
        Path(settings.AGENT_WORKSPACE_DIR),
        Path(__file__).resolve().parents[2] / "agent_works",
        Path(settings.WORKSPACE_ROOT),
    ]

    for root in roots:
        names = [f"task_{task_id}", f"task-{task_id}"]
        if execution_id is not None:
            names.extend([f"task_{execution_id}", f"task-{execution_id}"])
        for name in names:
            candidates.append(root / name)

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def resolve_legacy_workspace(
    task_id: int,
    execution_id: int | None = None,
    workspace_path: str | None = None,
) -> Path:
    candidates = legacy_workspace_candidates(
        task_id=task_id,
        execution_id=execution_id,
        workspace_path=workspace_path,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def read_legacy_progress_summary(workspace: Path) -> dict[str, Any] | None:
    progress_file = workspace / "progress.jsonl"
    if not progress_file.exists():
        return None

    try:
        lines = [ln for ln in progress_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return None

    for raw in reversed(lines):
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue

        phase = str(item.get("phase") or "").lower() or None
        progress_pct = float(item.get("progress_pct") or 0)
        status = None
        if phase == "failed":
            status = OrchTaskStatus.FAILED.value
        elif phase in {"completed", "complete", "delivered"}:
            status = OrchTaskStatus.COMPLETED.value
        elif phase in {"planning", "execution", "review", "deployment", "deploying", "delivery"}:
            status = OrchTaskStatus.EXECUTING.value

        return {
            "current_phase": phase,
            "progress_pct": progress_pct,
            "status": status,
        }

    return None


def read_legacy_plan_subtasks(workspace: Path) -> list[dict[str, Any]]:
    plan_file = workspace / ".implementation_plan.json"
    if not plan_file.exists():
        return []

    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return []

    state: dict[str, Any] = {}
    state_file = workspace / ".swarm_state.json"
    if state_file.exists():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except Exception:
            state = {}

    completed_step_nums = {
        int(step.get("step_number"))
        for step in state.get("completed_steps", [])
        if isinstance(step, dict) and step.get("step_number") is not None
    }

    next_pending_num: int | None = None
    for idx, step in enumerate(raw_steps, start=1):
        step_num = int(step.get("step_number") or idx)
        if step_num not in completed_step_nums:
            next_pending_num = step_num
            break

    state_status = str(state.get("status") or "").lower()
    subtasks: list[dict[str, Any]] = []
    for idx, step in enumerate(raw_steps, start=1):
        step_num = int(step.get("step_number") or idx)
        title = str(step.get("description") or f"Step {step_num}").strip()
        description = str(step.get("commit_message") or "").strip()

        if step_num in completed_step_nums:
            status = "completed"
        elif state_status == "failed" and next_pending_num == step_num:
            status = "failed"
        elif next_pending_num == step_num:
            status = "in_progress"
        else:
            status = "pending"

        subtasks.append(
            {
                "id": step_num,
                "order_index": idx,
                "title": title,
                "description": description,
                "status": status,
                "result": None,
                "files_changed": [],
            }
        )

    return subtasks


async def ensure_legacy_execution(
    session: AsyncSession,
    *,
    task_id: int,
    task_snapshot: dict[str, Any] | None = None,
    default_status: str = OrchTaskStatus.PLANNING.value,
) -> OrchTaskExecution:
    result = await session.execute(
        select(OrchTaskExecution)
        .where(OrchTaskExecution.oriexa_task_id == task_id)
        .limit(1)
    )
    execution = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    workspace = resolve_legacy_workspace(task_id=task_id)

    if execution is not None:
        if not execution.workspace_path:
            execution.workspace_path = str(workspace)
        if task_snapshot and not execution.task_snapshot:
            execution.task_snapshot = task_snapshot
        if execution.status in {OrchTaskStatus.PENDING.value, OrchTaskStatus.CLAIMING.value}:
            execution.status = default_status
        if execution.started_at is None:
            execution.started_at = now
        execution.updated_at = now
        await session.flush()
        return execution

    execution = OrchTaskExecution(
        oriexa_task_id=task_id,
        status=default_status,
        task_snapshot=task_snapshot or {},
        graph_thread_id=f"legacy-worker-{task_id}",
        workspace_path=str(workspace),
        started_at=now,
        updated_at=now,
    )
    session.add(execution)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        result = await session.execute(
            select(OrchTaskExecution)
            .where(OrchTaskExecution.oriexa_task_id == task_id)
            .limit(1)
        )
        existing = result.scalar_one()
        return existing

    return execution
