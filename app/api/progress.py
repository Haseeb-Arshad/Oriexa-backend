"""SSE (Server-Sent Events) endpoint for live task progress streaming."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

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
