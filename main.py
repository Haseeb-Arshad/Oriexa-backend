#!/usr/bin/env python3
"""
Oriexa Orchestrator API — FastAPI backend (port 8000)

Serves orchestrator endpoints that the frontend activity tab polls for
agent execution status, planning data, and real-time progress streaming.

Reads from the shared agent_works/ directory written by the Python agent swarm.

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Or with the startup script:
    python main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

WORKSPACE_DIR = Path(
    os.environ.get("AGENT_WORKSPACE_DIR", str(Path(__file__).parent / "agent_works"))
)
NEXT_APP_URL = os.environ.get("NEXT_APP_URL", "http://localhost:3000")
PORT = int(os.environ.get("PORT", 8000))

app = FastAPI(
    title="Oriexa Orchestrator API",
    description="Backend for Oriexa agent execution tracking and progress streaming",
    version="1.0.0",
)

# CORS — allow Next.js frontend
_extra_origins = [o.strip() for o in os.environ.get("EXTRA_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        NEXT_APP_URL,
        "http://localhost:3000",
        "http://localhost:3001",
        "https://*.vercel.app",
        *_extra_origins,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def get_task_dir(task_id: int) -> Path:
    return WORKSPACE_DIR / f"task_{task_id}"


def read_state(task_id: int) -> dict | None:
    state_file = get_task_dir(task_id) / ".swarm_state.json"
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_plan(task_id: int) -> dict | None:
    plan_file = get_task_dir(task_id) / ".implementation_plan.json"
    if not plan_file.exists():
        return None
    try:
        return json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def ok(data: dict | list) -> dict:
    return {"ok": True, "data": data}


def err(message: str, code: int = 404) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=code)


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/orchestrator/tasks/by-task/{task_id}/active")
async def get_active_execution(task_id: int):
    """Return the active execution ID for a task (1:1 with task_id)."""
    task_dir = get_task_dir(task_id)
    state = read_state(task_id)

    # If the workspace dir exists but no state file yet, the Coder hasn't started
    # writing yet but is likely queued. Return a "queued" execution so the frontend
    # gets an execution_id and the SSE stream can connect and wait for data.
    if state is None:
        if task_dir.exists():
            return ok({
                "execution_id": task_id,
                "task_id": task_id,
                "status": "queued",
                "started_at": None,
                "workspace_path": str(task_dir),
            })
        return err(f"No active execution for task {task_id}")

    return ok({
        "execution_id": task_id,
        "task_id": task_id,
        "status": state.get("status", "coding"),
        "started_at": state.get("started_at"),
        "workspace_path": str(task_dir),
    })


@app.get("/orchestrator/tasks/{execution_id}")
async def get_execution(execution_id: int):
    """Return execution metadata and status."""
    task_id = execution_id  # 1:1 mapping
    task_dir = get_task_dir(task_id)
    state = read_state(task_id)
    if state is None:
        if task_dir.exists():
            return ok({
                "id": execution_id,
                "status": "queued",
                "total_tokens_used": 0,
                "total_cost_usd": None,
                "attempt_count": 0,
                "started_at": None,
                "completed_at": None,
                "workspace_path": str(task_dir),
                "error_message": None,
                "plan": None,
            })
        return err(f"Execution {execution_id} not found")

    # Count progress lines for token estimate
    total_lines = 0
    progress_file = get_task_dir(task_id) / "progress.jsonl"
    if progress_file.exists():
        try:
            content = progress_file.read_text(encoding="utf-8")
            total_lines = len([l for l in content.split("\n") if l.strip()])
        except Exception:
            pass

    pipeline_status = state.get("status", "coding")
    is_complete = pipeline_status in ("deployed", "complete", "completed")
    is_active = not is_complete

    return ok({
        "id": execution_id,
        "status": "completed" if is_complete else "in_progress",
        "total_tokens_used": total_lines * 1200,
        "total_cost_usd": None,
        "attempt_count": state.get("iterations", 1),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at") if is_complete else None,
        "workspace_path": str(get_task_dir(task_id)),
        "error_message": state.get("last_error"),
        "plan": state.get("plan"),
    })


@app.get("/orchestrator/preview/executions/{execution_id}")
async def get_execution_preview(execution_id: int):
    """Return the plan steps as subtasks for the journey map UI."""
    task_id = execution_id
    task_dir = get_task_dir(task_id)
    state = read_state(task_id)
    if state is None:
        if task_dir.exists():
            # Coder hasn't started yet — show a "queued" planning step
            return ok({"execution_id": execution_id, "subtasks": [
                {
                    "id": 0, "order_index": 0,
                    "title": "Planning",
                    "description": "Agent is starting up and creating an implementation plan...",
                    "status": "in_progress",
                    "result": None, "files_changed": None,
                }
            ]})
        return err(f"Execution {execution_id} not found")

    plan = read_plan(task_id) or state.get("plan")
    completed_step_nums = set(
        s["step_number"] for s in (state.get("completed_steps") or [])
    )
    current_step = state.get("current_step", 0)
    pipeline_status = state.get("status", "coding")

    subtasks = []

    # Planning subtask
    subtasks.append({
        "id": 0,
        "order_index": 0,
        "title": "Planning",
        "description": (
            f"{plan.get('project_type', 'Project')} — {len(plan.get('steps', []))} steps planned"
            if plan else "Analyzing task and creating implementation plan"
        ),
        "status": "completed" if plan else ("in_progress" if pipeline_status == "planning" else "pending"),
        "result": f"{len(plan.get('steps', []))} steps" if plan else None,
        "files_changed": None,
    })

    # Implementation steps
    if plan and plan.get("steps"):
        for step in plan["steps"]:
            step_num = step.get("step_number", 0)
            is_done = step_num in completed_step_nums
            is_current = step_num == current_step + 1 and not is_done and pipeline_status == "coding"

            completed = next(
                (s for s in (state.get("completed_steps") or []) if s["step_number"] == step_num),
                None,
            )

            subtasks.append({
                "id": step_num,
                "order_index": step_num,
                "title": step.get("description", f"Step {step_num}"),
                "description": (
                    "Files: " + ", ".join(f["path"] for f in (step.get("files") or []))
                    if step.get("files") else step.get("description", "")
                ),
                "status": "completed" if is_done else "in_progress" if is_current else "pending",
                "result": completed.get("commit") if completed else None,
                "files_changed": completed.get("files_written") if completed else None,
            })
    elif pipeline_status == "coding":
        subtasks.append({
            "id": 1, "order_index": 1,
            "title": "Implementation",
            "description": "Writing code and building the solution",
            "status": "in_progress", "result": None, "files_changed": None,
        })

    # Testing / Deploying subtasks
    if pipeline_status in ("testing", "deploying", "deployed"):
        subtasks.append({
            "id": 100, "order_index": 100,
            "title": "Testing",
            "description": "Running tests to validate implementation",
            "status": (
                "in_progress" if pipeline_status == "testing"
                else "completed" if pipeline_status in ("deploying", "deployed")
                else "pending"
            ),
            "result": None, "files_changed": None,
        })

    if pipeline_status in ("deploying", "deployed"):
        subtasks.append({
            "id": 101, "order_index": 101,
            "title": "Deployment",
            "description": "Deploying project to production",
            "status": "in_progress" if pipeline_status == "deploying" else "completed",
            "result": state.get("deploy_url"),
            "files_changed": None,
        })

    return ok({"execution_id": execution_id, "subtasks": subtasks})


@app.get("/orchestrator/tasks/{execution_id}/logs")
async def get_execution_logs(execution_id: int):
    """Return raw build log content for a task execution."""
    task_id = execution_id
    task_dir = get_task_dir(task_id)
    if not task_dir.exists():
        return err(f"Execution {execution_id} not found")

    log_file = task_dir / ".build_log"
    if not log_file.exists():
        return ok(None)

    try:
        content = log_file.read_text(encoding="utf-8", errors="replace")
        # Return last 8000 chars to avoid huge payloads
        if len(content) > 8000:
            content = "... (truncated) ...\n" + content[-8000:]
        return ok(content)
    except Exception:
        return ok(None)


@app.get("/orchestrator/progress/executions/{execution_id}/stream")
async def stream_progress(execution_id: int, request: Request):
    """Server-Sent Events stream of progress steps from progress.jsonl."""
    task_id = execution_id
    progress_file = get_task_dir(task_id) / "progress.jsonl"

    async def event_generator():
        last_line_count = 0
        idle_count = 0
        max_idle = 300  # Stop after 10 min of no new data (300 * 2s)

        while True:
            if await request.is_disconnected():
                break

            new_lines = []
            if progress_file.exists():
                try:
                    content = progress_file.read_text(encoding="utf-8")
                    lines = [l for l in content.split("\n") if l.strip()]
                    new_lines = lines[last_line_count:]
                    last_line_count = len(lines)
                except Exception:
                    pass

            for line in new_lines:
                try:
                    step = json.loads(line)
                    yield f"event: progress\ndata: {json.dumps(step)}\n\n"
                    idle_count = 0
                except Exception:
                    pass

            if not new_lines:
                # Send keepalive
                yield ": keepalive\n\n"
                idle_count += 1
                if idle_count > max_idle:
                    break

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok", "service": "oriexa-api", "workspace": str(WORKSPACE_DIR)}


@app.get("/")
async def root():
    return {
        "service": "Oriexa Orchestrator API",
        "version": "1.0.0",
        "endpoints": [
            "/orchestrator/tasks/by-task/{task_id}/active",
            "/orchestrator/tasks/{execution_id}",
            "/orchestrator/preview/executions/{execution_id}",
            "/orchestrator/progress/executions/{execution_id}/stream",
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"Starting Oriexa Orchestrator API on port {PORT}")
    print(f"Workspace: {WORKSPACE_DIR}")
    print(f"Next.js origin: {NEXT_APP_URL}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False)
