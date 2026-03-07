"""SSE (Server-Sent Events) endpoint for live task progress streaming."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.responses import StreamingResponse

from app.config import settings
from app.db.engine import async_session
from app.db.models import OrchTaskExecution
from app.orchestrator.progress import progress_tracker

router = APIRouter(prefix="/orchestrator/progress", tags=["progress"])


@router.get("/executions/{execution_id}/stream")
async def stream_progress(execution_id: int):
    """Stream live progress events via SSE for a specific execution.

    Returns a text/event-stream response. Each event is a JSON object with:
    - phase, title, description, detail, progress_pct, timestamp
    """

    async def event_generator():
        # First send all existing steps as a burst
        existing = progress_tracker.get_steps(execution_id)
        if existing:
            for i, step in enumerate(existing):
                yield _format_sse(step, i)
        else:
            # Fallback for legacy pipeline: stream progress.jsonl directly.
            legacy_file = await _resolve_legacy_progress_file(execution_id)
            if legacy_file and legacy_file.exists():
                last_line_count = 0
                idle = 0
                while True:
                    try:
                        content = legacy_file.read_text(encoding="utf-8")
                        lines = [ln for ln in content.splitlines() if ln.strip()]
                    except Exception:
                        lines = []

                    while last_line_count < len(lines):
                        raw = lines[last_line_count]
                        last_line_count += 1
                        try:
                            payload = json.loads(raw)
                        except Exception:
                            continue
                        if "index" not in payload:
                            payload["index"] = last_line_count - 1
                        yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                        idle = 0

                    if lines:
                        last = _safe_json_line(lines[-1])
                        phase = str(last.get("phase", "")).lower() if last else ""
                        if phase in {"delivery", "failed", "complete", "completed", "delivered"}:
                            return

                    idle += 1
                    yield f": heartbeat {int(time.time())}\n\n"
                    if idle > 300:
                        return
                    await asyncio.sleep(2)
                return

        # Then stream new steps as they arrive
        last_idx = len(existing)
        async for idx, step in progress_tracker.subscribe(execution_id, last_idx):
            if step is None:
                # Heartbeat
                yield f": heartbeat {int(time.time())}\n\n"
            else:
                yield _format_sse(step, idx)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/executions/{execution_id}")
async def get_progress(execution_id: int) -> dict[str, Any]:
    """Get all progress steps for an execution (non-streaming)."""
    steps = progress_tracker.get_steps(execution_id)
    if not steps:
        legacy_steps = await _load_legacy_steps(execution_id)
        if legacy_steps:
            phases = {"delivery", "failed", "complete", "completed", "delivered"}
            latest_phase = str(legacy_steps[-1].get("phase", "")).lower()
            return {
                "ok": True,
                "data": {
                    "execution_id": execution_id,
                    "steps": legacy_steps,
                    "is_complete": latest_phase in phases,
                    "current_phase": legacy_steps[-1].get("phase"),
                },
            }

    return {
        "ok": True,
        "data": {
            "execution_id": execution_id,
            "steps": [
                {
                    "index": i,
                    "phase": s.phase,
                    "title": s.title,
                    "description": s.description,
                    "detail": s.detail,
                    "progress_pct": s.progress_pct,
                    "timestamp": s.timestamp,
                    "metadata": s.metadata,
                }
                for i, s in enumerate(steps)
            ],
            "is_complete": bool(steps and steps[-1].phase in ("delivery", "failed")),
            "current_phase": steps[-1].phase if steps else None,
        },
    }


@router.get("/active")
async def list_active() -> dict[str, Any]:
    """List all executions with active progress tracking."""
    active = progress_tracker.get_active_executions()
    result = []
    for eid in active:
        steps = progress_tracker.get_steps(eid)
        latest = steps[-1] if steps else None
        result.append({
            "execution_id": eid,
            "current_phase": latest.phase if latest else None,
            "progress_pct": latest.progress_pct if latest else 0,
            "description": latest.description if latest else "",
            "step_count": len(steps),
            "is_complete": bool(latest and latest.phase in ("delivery", "failed")),
        })
    return {"ok": True, "data": result}


def _format_sse(step, index: int) -> str:
    """Format a progress step as an SSE event string."""
    data = json.dumps({
        "index": index,
        "phase": step.phase,
        "title": step.title,
        "description": step.description,
        "detail": step.detail,
        "progress_pct": step.progress_pct,
        "timestamp": step.timestamp,
        "metadata": step.metadata,
    })
    return f"event: progress\ndata: {data}\n\n"


def _safe_json_line(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def _resolve_legacy_progress_file(execution_id: int) -> Path | None:
    async with async_session() as session:
        result = await session.execute(
            select(OrchTaskExecution).where(OrchTaskExecution.id == execution_id).limit(1)
        )
        execution = result.scalar_one_or_none()

    if not execution:
        return None

    candidates: list[Path] = []
    if execution.workspace_path:
        candidates.append(Path(execution.workspace_path) / "progress.jsonl")

    workspace_roots = {
        Path(settings.WORKSPACE_ROOT),
        Path(settings.AGENT_WORKSPACE_DIR),
        Path(__file__).resolve().parents[2] / "agent_works",
    }
    for root in workspace_roots:
        candidates.extend([
            root / f"task-{execution_id}" / "progress.jsonl",
            root / f"task_{execution_id}" / "progress.jsonl",
            root / f"task-{execution.oriexa_task_id}" / "progress.jsonl",
            root / f"task_{execution.oriexa_task_id}" / "progress.jsonl",
        ])

    for c in candidates:
        if c.exists():
            return c
    return None


async def _load_legacy_steps(execution_id: int) -> list[dict[str, Any]]:
    progress_file = await _resolve_legacy_progress_file(execution_id)
    if not progress_file or not progress_file.exists():
        return []

    try:
        lines = [ln for ln in progress_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return []

    parsed: list[dict[str, Any]] = []
    for idx, raw in enumerate(lines):
        item = _safe_json_line(raw)
        if not item:
            continue
        item.setdefault("index", idx)
        item.setdefault("phase", "")
        item.setdefault("title", "")
        item.setdefault("description", "")
        item.setdefault("detail", "")
        item.setdefault("progress_pct", 0)
        item.setdefault("timestamp", time.time())
        item.setdefault("metadata", {})
        parsed.append(item)
    return parsed
