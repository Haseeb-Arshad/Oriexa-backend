"""LangGraph supervisor graph — orchestrates agents through the task pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from langgraph.graph import END, StateGraph
from sqlalchemy import select

from app.config import settings
from app.orchestrator.progress import progress_tracker
from app.orchestrator.state import TaskState
from app.orchestrator.git_helper import GitHelper

logger = logging.getLogger(__name__)


def _eid(state: TaskState) -> int:
    """Extract execution_id from state."""
    return state.get("execution_id", 0)


def _normalize_subtask_status(status: str | None) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"pending", "in_progress", "completed", "failed", "skipped"}:
        return normalized
    return "completed" if normalized in {"done", "success"} else "failed"


def _sanitize_plan_item(index: int, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title", f"Subtask {index + 1}")),
        "description": str(item.get("description", "")),
        "depends_on": list(item.get("depends_on", [])),
    }


def _merge_plan_patch(
    existing_plan: list[dict[str, Any]],
    proposed_plan: list[dict[str, Any]],
    subtask_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Patch an existing plan without rewriting completed work."""
    if not existing_plan:
        return [_sanitize_plan_item(i, p) for i, p in enumerate(proposed_plan)]
    if not proposed_plan:
        return [_sanitize_plan_item(i, p) for i, p in enumerate(existing_plan)]

    completed_indexes = {
        int(item.get("index", -1))
        for item in subtask_results
        if _normalize_subtask_status(item.get("status")) == "completed"
    }

    merged = [_sanitize_plan_item(i, p) for i, p in enumerate(existing_plan)]
    for i, proposed in enumerate(proposed_plan):
        normalized = _sanitize_plan_item(i, proposed)
        if i < len(merged):
            if i in completed_indexes:
                # Keep completed work untouched.
                continue
            current = merged[i]
            merged[i] = {
                "title": current["title"] or normalized["title"],
                "description": normalized["description"] or current["description"],
                "depends_on": normalized["depends_on"] or current["depends_on"],
            }
        else:
            merged.append(normalized)
    return merged


_HOUSEKEEPING_FILES = {
    ".gitignore",
    ".build_log",
    ".swarm_state.json",
}


_MEANINGFUL_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php",
    ".html", ".css", ".scss", ".sql", ".json", ".yaml", ".yml", ".md",
}


_IGNORED_SCAN_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "dist",
    "build",
}


def _has_meaningful_file_changes(
    files_created: list[str],
    files_modified: list[str],
) -> bool:
    for path in [*files_created, *files_modified]:
        if not path:
            continue
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name in _HOUSEKEEPING_FILES:
            continue
        suffix = ""
        if "." in name and not name.startswith("."):
            suffix = "." + name.rsplit(".", 1)[-1].lower()
        if suffix in _MEANINGFUL_EXTENSIONS or suffix == "":
            return True
    return False


def _has_meaningful_workspace_files(workspace_path: str) -> bool:
    """Fallback check when agent metadata misses file edits.

    Some implementations create files via shell commands instead of write_file tool calls,
    so files_created/files_modified may be empty even when real code exists.
    """
    if not workspace_path:
        return False

    root = Path(workspace_path)
    if not root.exists() or not root.is_dir():
        return False

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if any(part in _IGNORED_SCAN_DIRS for part in parts):
            continue
        name = path.name
        if name in _HOUSEKEEPING_FILES:
            continue

        suffix = path.suffix.lower()
        if suffix in _MEANINGFUL_EXTENSIONS:
            return True
        if suffix == "" and name not in {".env", ".npmrc"}:
            return True

    return False


async def _persist_subtask_results(
    execution_id: int,
    subtask_results: list[dict[str, Any]],
) -> None:
    if execution_id <= 0:
        return
    try:
        from app.db.engine import async_session
        from app.db.models import OrchSubtask

        async with async_session() as session:
            result = await session.execute(
                select(OrchSubtask).where(OrchSubtask.execution_id == execution_id)
            )
            rows = {row.order_index: row for row in result.scalars().all()}
            for item in subtask_results:
                idx = int(item.get("index", -1))
                row = rows.get(idx)
                if row is None:
                    continue
                row.status = _normalize_subtask_status(item.get("status"))
                row.result = str(item.get("result", ""))[:4000]
                files_changed = item.get("files_changed")
                row.files_changed = files_changed if isinstance(files_changed, list) else []
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to persist subtask results for execution %d: %s", execution_id, exc)


async def _upsert_plan_subtasks(
    execution_id: int,
    plan: list[dict[str, Any]],
) -> None:
    """Persist planning subtasks without destructive rewrites."""
    if execution_id <= 0:
        return
    try:
        from app.db.engine import async_session
        from app.db.models import OrchSubtask

        async with async_session() as session:
            result = await session.execute(
                select(OrchSubtask)
                .where(OrchSubtask.execution_id == execution_id)
                .order_by(OrchSubtask.order_index)
            )
            rows = {row.order_index: row for row in result.scalars().all()}
            for idx, item in enumerate(plan):
                normalized = _sanitize_plan_item(idx, item)
                row = rows.get(idx)
                if row is None:
                    session.add(
                        OrchSubtask(
                            execution_id=execution_id,
                            order_index=idx,
                            title=normalized["title"],
                            description=normalized["description"],
                            status="pending",
                            depends_on=normalized["depends_on"],
                        )
                    )
                    continue

                # Completed/in-progress items are immutable; patch only pending/failed/skipped.
                if row.status in {"completed", "in_progress"}:
                    if not row.depends_on and normalized["depends_on"]:
                        row.depends_on = normalized["depends_on"]
                    continue

                row.title = normalized["title"]
                row.description = normalized["description"]
                row.depends_on = normalized["depends_on"]
                if row.status == "skipped":
                    row.status = "pending"

            await session.commit()
    except Exception as exc:
        logger.warning("Failed to upsert OrchSubtask records for execution %d: %s", execution_id, exc)


# ---------------------------------------------------------------------------
# Helper: fetch messages for a task via the Oriexa API
# ---------------------------------------------------------------------------

async def _fetch_messages_for_task(task_id: int) -> list[dict[str, Any]]:
    """Fetch all messages for a task from the Oriexa API."""
    base_url = settings.ORIEXA_API_BASE_URL.rstrip("/")
    api_key = settings.ORIEXA_API_KEY
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base_url}/tasks/{task_id}/messages",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            body = resp.json()
            # Handle multiple response formats:
            #   {"messages": [...]}  — Next.js user API format
            #   {"data": [...]}      — generic wrapper format
            #   [...]                — raw array
            if isinstance(body, list):
                return body
            if isinstance(body, dict):
                for key in ("messages", "data"):
                    candidate = body.get(key)
                    if isinstance(candidate, list):
                        return candidate
            logger.warning("Unexpected messages response format for task %d: %s", task_id, type(body))
            return []
    except Exception as exc:
        logger.warning("Failed to fetch messages for task %d: %s", task_id, exc)
        return []


async def _update_execution_status(execution_id: int, status: str) -> None:
    """Update the orchestrator execution status in the database."""
    try:
        from app.db.engine import async_session
        from app.db.models import OrchestratorExecution
        from sqlalchemy import update
        from datetime import datetime, timezone

        async with async_session() as session:
            await session.execute(
                update(OrchestratorExecution)
                .where(OrchestratorExecution.id == execution_id)
                .values(status=status, updated_at=datetime.now(timezone.utc))
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to update execution %d status to %s: %s", execution_id, status, exc)


# ---------------------------------------------------------------------------
# Node functions — each invokes the corresponding agent
# ---------------------------------------------------------------------------

async def triage_node(state: TaskState) -> dict[str, Any]:
    """Run the TriageAgent to assess task clarity and complexity."""
    from app.agents.triage import TriageAgent

    eid = _eid(state)
    task_title = state.get("task_data", {}).get("title", "this task")
    progress_tracker.add_step(eid, "triage", "start",
        detail=f"Taking a close look at \"{task_title}\" to understand the requirements")
    progress_tracker.add_step(eid, "triage", "thinking",
        detail="Assessing clarity, complexity, and whether any questions need to be asked first")

    agent = TriageAgent()
    result = await agent.run(state)

    complexity = result.get("complexity", "medium")
    needs_clarification = result.get("needs_clarification", False)
    reasoning = result.get("reasoning", "")

    detail = f"Complexity: {complexity}."
    if needs_clarification:
        detail += " Some things need clarification before diving in."
    else:
        detail += " Everything looks clear — ready to start planning."

    progress_tracker.add_step(eid, "triage", "done", detail=detail,
        metadata={"complexity": complexity, "clarity_score": result.get("clarity_score", 0)})

    # Extract task_type if the triage agent returned it
    task_type = result.get("task_type", "general")
    if task_type not in ("frontend", "backend", "fullstack", "general"):
        task_type = "general"

    return {
        "phase": "triage",
        "clarity_score": result.get("clarity_score", 0.5),
        "complexity": complexity,
        "needs_clarification": needs_clarification,
        "triage_reasoning": reasoning,
        "task_type": task_type,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def clarification_node(state: TaskState) -> dict[str, Any]:
    """Run the ClarificationAgent to post questions to the poster."""
    from app.agents.clarification import ClarificationAgent

    eid = _eid(state)
    progress_tracker.add_step(eid, "clarification", "start",
        detail="A few details could make the difference between good and great")
    progress_tracker.add_step(eid, "clarification", "thinking",
        detail="Formulating precise questions to fill in the gaps")

    agent = ClarificationAgent()
    result = await agent.run(state)

    questions = result.get("questions", [])
    clarification_needed = result.get("clarification_needed", True)
    message_id = result.get("clarification_message_id")
    message_ids = result.get("clarification_message_ids", [])
    question_summary = result.get("question_summary", "")

    q_count = len(message_ids) if message_ids else (1 if message_id else 0)

    if clarification_needed and message_id:
        progress_tracker.add_step(eid, "clarification", "done",
            detail=f"Posted {q_count} question(s) to the poster — {question_summary}",
            metadata={"question_count": q_count, "message_ids": message_ids})
    else:
        progress_tracker.add_step(eid, "clarification", "done",
            detail="Task is clear enough to proceed directly to planning",
            metadata={"question_count": 0})

    return {
        "phase": "clarification",
        "clarification_questions": questions,
        "clarification_message_sent": clarification_needed and message_id is not None,
        "clarification_message_id": message_id,
        "clarification_message_ids": message_ids,
        "waiting_for_response": clarification_needed and message_id is not None,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


def _check_messages_for_response(
    messages: list[dict[str, Any]],
    question_message_ids: list[int],
) -> str | None:
    """Check fetched messages for a poster response to any of the question IDs.

    Detection tiers (checked in order):
    1. structured_data.responded_at on any question message (UI button response)
    2. Poster reply with parent_id matching any question message
    3. Any poster message with id > smallest question message id

    Returns the response text if found, else None.
    """
    if not question_message_ids:
        return None

    min_question_id = min(question_message_ids)

    # Tier 1: structured_data.responded_at on any question message
    for msg in messages:
        if msg.get("id") in question_message_ids:
            sd = msg.get("structured_data") or {}
            if sd.get("responded_at"):
                return sd.get("response", "")

    # Tier 2: poster reply with parent_id matching any question
    for msg in messages:
        if (msg.get("sender_type") == "poster"
                and msg.get("parent_id") in question_message_ids):
            return msg.get("content", "")

    # Tier 3: any poster message posted after the earliest question
    poster_msgs = [
        m for m in messages
        if m.get("sender_type") == "poster"
        and isinstance(m.get("id"), int)
        and m["id"] > min_question_id
    ]
    if poster_msgs:
        # Concatenate all poster responses (they may have answered multiple questions)
        return "\n".join(m.get("content", "") for m in poster_msgs if m.get("content"))

    return None


async def wait_for_response_node(state: TaskState) -> dict[str, Any]:
    """Poll for the poster's response to clarification questions.

    Checks every 15 seconds for up to 15 minutes. Detects responses via:
    1. structured_data.responded_at on any question message (UI interaction)
    2. Poster reply message with parent_id matching any question
    3. Any poster message posted after the questions
    """
    message_id = state.get("clarification_message_id")
    message_ids = state.get("clarification_message_ids", [])
    task_id = state.get("oriexa_task_id") or state.get("task_data", {}).get("id")
    execution_id = state.get("execution_id", 0)

    # Build the full list of question IDs to track
    all_question_ids = list(message_ids) if message_ids else []
    if message_id and message_id not in all_question_ids:
        all_question_ids.append(message_id)

    # Set CLARIFYING status so UI can show it
    await _update_execution_status(execution_id, "clarifying")

    progress_tracker.add_step(execution_id, "clarification", "waiting",
        detail=f"Waiting for the poster to respond ({len(all_question_ids)} question(s) posted)...")

    if not task_id:
        logger.warning("wait_for_response_node: no task_id in state")
        return {"waiting_for_response": False, "clarification_response": None, "phase": "planning"}

    logger.info(
        "wait_for_response_node: task_id=%s question_ids=%s — starting poll",
        task_id, all_question_ids,
    )

    # Poll every 15s for up to 15 minutes (60 iterations)
    max_polls = 60
    poll_interval = 15

    for poll in range(max_polls):
        messages = await _fetch_messages_for_task(task_id)

        if poll == 0:
            logger.info(
                "wait_for_response_node: poll #%d fetched %d messages for task %s",
                poll, len(messages), task_id,
            )

        response = _check_messages_for_response(messages, all_question_ids)
        if response is not None:
            logger.info(
                "wait_for_response_node: response detected on poll #%d for task %s",
                poll, task_id,
            )
            progress_tracker.add_step(execution_id, "clarification", "responded",
                detail=f"Poster responded: {response[:100]}{'...' if len(response) > 100 else ''}")
            return {
                "waiting_for_response": False,
                "clarification_response": response,
                "phase": "planning",
            }

        await asyncio.sleep(poll_interval)

    # Timeout — proceed without response
    logger.warning(
        "wait_for_response_node: timeout after %d polls for task %s",
        max_polls, task_id,
    )
    progress_tracker.add_step(execution_id, "clarification", "timeout",
        detail="No response received after 15 minutes — proceeding with planning based on available info")

    return {"waiting_for_response": False, "clarification_response": None, "phase": "planning"}


async def planning_node(state: TaskState) -> dict[str, Any]:
    """Run the PlanningAgent to decompose the task into subtasks."""
    from app.agents.planning import PlanningAgent
    from app.llm.router import ModelTier

    eid = _eid(state)
    existing_plan = state.get("plan", []) or []
    planning_locked = bool(state.get("planning_locked", False))
    replan_requested = bool(state.get("replan_requested", False))

    if existing_plan and planning_locked and not replan_requested:
        progress_tracker.add_step(
            eid,
            "planning",
            "start",
            detail="Plan already exists and is locked; reusing current roadmap",
        )
        await _upsert_plan_subtasks(eid, existing_plan)
        subtask_titles = [s.get("title", "Step") for s in existing_plan]
        progress_tracker.add_step(
            eid,
            "planning",
            "done",
            detail=f"Continuing with existing {len(existing_plan)}-step plan: {', '.join(subtask_titles[:4])}{'...' if len(subtask_titles) > 4 else ''}",
            metadata={"subtask_count": len(existing_plan), "subtasks": subtask_titles, "reused": True},
        )
        return {
            "phase": "planning",
            "plan": existing_plan,
            "planning_locked": True,
            "replan_requested": False,
            "replan_reason": None,
            "current_subtask_index": state.get("current_subtask_index", 0),
            "subtask_results": state.get("subtask_results", []),
            "total_prompt_tokens": state.get("total_prompt_tokens", 0),
            "total_completion_tokens": state.get("total_completion_tokens", 0),
        }

    attempt = state.get("attempt_count", 0)
    if attempt > 0:
        progress_tracker.add_step(eid, "planning", "start",
            detail=f"Taking another pass (attempt {attempt + 1}) with fresh insights from the review")
    else:
        progress_tracker.add_step(eid, "planning", "start",
            detail="Designing a step-by-step blueprint to build this the right way")

    progress_tracker.add_step(eid, "planning", "exploring",
        detail="Scanning the workspace, reading existing files, mapping out the landscape")

    # Feed clarification response into the state for the planning agent
    clarification_response = state.get("clarification_response")
    replan_reason = state.get("replan_reason")
    planning_state = dict(state)
    if clarification_response:
        original_desc = planning_state.get("task_data", {}).get("description", "")
        planning_state.setdefault("task_data", {})
        planning_state["task_data"] = dict(planning_state["task_data"])
        planning_state["task_data"]["description"] = (
            f"Poster clarified: {clarification_response}\n\n{original_desc}"
        )
    if replan_reason:
        original_desc = planning_state.get("task_data", {}).get("description", "")
        planning_state.setdefault("task_data", {})
        planning_state["task_data"] = dict(planning_state["task_data"])
        planning_state["task_data"]["description"] = (
            f"User intervention update: {replan_reason}\n\n{original_desc}"
        )

    # Frontend tasks use claude-sonnet-4.6 for deep planning reasoning
    task_type = state.get("task_type", "general")
    planning_tier = (
        ModelTier.CODING_PLANNING.value
        if task_type == "frontend"
        else ModelTier.DEFAULT.value
    )
    agent = PlanningAgent(model_tier=planning_tier)
    result = await agent.run(planning_state)

    proposed_plan = result.get("plan", [])
    if existing_plan and planning_locked:
        plan = _merge_plan_patch(
            existing_plan,
            proposed_plan,
            state.get("subtask_results", []),
        )
    else:
        plan = [_sanitize_plan_item(i, p) for i, p in enumerate(proposed_plan)]
    plan_revision_count = int(state.get("plan_revision_count", 0))
    if existing_plan and planning_locked and replan_requested:
        plan_revision_count += 1

    subtask_titles = [s.get("title", "Step") for s in plan]

    # ── Persist OrchSubtask records to database ───────────────────
    # The frontend roadmap reads from the orch_subtasks table.
    # Without these rows, the UI shows "Spinning up..." indefinitely.
    if eid and plan:
        await _upsert_plan_subtasks(eid, plan)
        logger.info("Persisted/updated %d OrchSubtask records for execution %d", len(plan), eid)

    # Git commit after planning
    workspace_path = state.get("workspace_path")
    if workspace_path:
        progress_tracker.add_step(eid, "planning", "committing",
            detail="Saving plan to version control")
        git = GitHelper(workspace_path)
        await git.add_commit_push(
            f"Phase: Planning - Created plan with {len(plan)} subtasks"
        )

    progress_tracker.add_step(eid, "planning", "done",
        detail=f"Created a {len(plan)}-step plan: {', '.join(subtask_titles[:4])}{'...' if len(plan) > 4 else ''}",
        metadata={"subtask_count": len(plan), "subtasks": subtask_titles})

    return {
        "phase": "planning",
        "plan": plan,
        "planning_locked": True,
        "plan_revision_count": plan_revision_count,
        "replan_requested": False,
        "replan_reason": None,
        "current_subtask_index": state.get("current_subtask_index", 0),
        "subtask_results": state.get("subtask_results", []),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def execution_node(state: TaskState) -> dict[str, Any]:
    """Run the ExecutionAgent to execute all subtasks."""
    from app.agents.execution import ExecutionAgent
    from app.llm.router import ModelTier

    eid = _eid(state)
    plan = state.get("plan", [])
    progress_tracker.add_step(eid, "execution", "start",
        detail=f"Executing {len(plan)} subtask(s) — writing code, running commands, building it out")

    progress_tracker.add_step(eid, "execution", "writing",
        detail="Fingers on keyboard — creating files, writing implementations, wiring things together")

    # Frontend tasks use z-ai/glm-5 as the primary execution model
    task_type = state.get("task_type", "general")
    exec_tier = (
        ModelTier.CODING.value
        if task_type == "frontend"
        else ModelTier.DEFAULT.value
    )
    agent = ExecutionAgent(model_tier=exec_tier)
    result = await agent.run(state)
    subtask_results = result.get("subtask_results", [])
    await _persist_subtask_results(eid, subtask_results)

    files_created = result.get("files_created", [])
    files_modified = result.get("files_modified", [])
    commands = result.get("commands_executed", [])

    progress_tracker.add_step(eid, "execution", "testing",
        detail=f"Verifying the work — {len(commands)} command(s) run, checking outputs")

    # Git commit after execution
    workspace_path = state.get("workspace_path")
    if workspace_path:
        progress_tracker.add_step(eid, "execution", "committing",
            detail="Committing changes to version control")
        git = GitHelper(workspace_path)
        await git.add_commit_push(
            f"Phase: Execution - {len(files_created)} files created, {len(files_modified)} files modified"
        )

    detail_parts = []
    if files_created:
        detail_parts.append(f"{len(files_created)} file(s) created")
    if files_modified:
        detail_parts.append(f"{len(files_modified)} file(s) modified")
    if commands:
        detail_parts.append(f"{len(commands)} command(s) executed")
    detail = ". ".join(detail_parts) + "." if detail_parts else "Implementation complete."

    progress_tracker.add_step(eid, "execution", "done", detail=detail,
        metadata={"files_created": len(files_created), "files_modified": len(files_modified)})

    return {
        "phase": "execution",
        "subtask_results": subtask_results,
        "files_created": files_created,
        "files_modified": files_modified,
        "commands_executed": commands,
        "deliverable_content": result.get("deliverable_summary", result.get("deliverable_content", "")),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def complex_execution_node(state: TaskState) -> dict[str, Any]:
    """Run the ComplexTaskAgent for high-complexity or high-budget tasks."""
    from app.agents.complex_task import ComplexTaskAgent
    from app.llm.router import ModelTier

    eid = _eid(state)
    plan = state.get("plan", [])
    progress_tracker.add_step(eid, "complex_execution", "start",
        detail=f"This one needs the full treatment — engaging deep reasoning for {len(plan)} subtask(s)")

    progress_tracker.add_step(eid, "complex_execution", "analyzing",
        detail="Thinking through edge cases, architecture patterns, and the cleanest path forward")

    progress_tracker.add_step(eid, "complex_execution", "writing",
        detail="Building the implementation with careful attention to every detail")

    # Frontend complex tasks escalate to gpt-5.3-codex (CODING_STRONG)
    task_type = state.get("task_type", "general")
    complex_tier = (
        ModelTier.CODING_STRONG.value
        if task_type == "frontend"
        else ModelTier.STRONG.value
    )
    agent = ComplexTaskAgent(model_tier=complex_tier)
    result = await agent.run(state)
    subtask_results = result.get("subtask_results", [])
    await _persist_subtask_results(eid, subtask_results)

    files_created = result.get("files_created", [])
    files_modified = result.get("files_modified", [])

    progress_tracker.add_step(eid, "complex_execution", "testing",
        detail="Running thorough tests and validation — making sure everything holds up")

    # Git commit after complex execution
    workspace_path = state.get("workspace_path")
    if workspace_path:
        progress_tracker.add_step(eid, "complex_execution", "committing",
            detail="Committing implementation to version control")
        git = GitHelper(workspace_path)
        await git.add_commit_push(
            f"Phase: Complex Execution - {len(files_created)} files created, {len(files_modified)} files modified"
        )

    progress_tracker.add_step(eid, "complex_execution", "done",
        detail=f"Deep work complete — {len(files_created)} file(s) created, {len(files_modified)} modified",
        metadata={"files_created": len(files_created), "files_modified": len(files_modified)})

    return {
        "phase": "execution",
        "subtask_results": subtask_results,
        "files_created": files_created,
        "files_modified": files_modified,
        "commands_executed": result.get("commands_executed", []),
        "deliverable_content": result.get("deliverable_summary", result.get("deliverable_content", "")),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def review_node(state: TaskState) -> dict[str, Any]:
    """Run the ReviewAgent to validate the deliverable."""
    from app.agents.review import ReviewAgent
    from app.tools.deployment import run_full_test_suite

    eid = _eid(state)
    workspace_path = state.get("workspace_path", "")

    progress_tracker.add_step(eid, "review", "start",
        detail="Putting on the reviewer hat — checking everything against the original requirements")
    
    # 1. Run full test suite to gate delivery
    progress_tracker.add_step(eid, "review", "testing",
        detail="Running lint, typecheck, unit tests, and build verification")

    test_results = {}
    if workspace_path:
        try:
            test_results = await run_full_test_suite(workspace_path)
            summary = test_results.get("summary", "No tests run")
            progress_tracker.add_step(eid, "review", "testing",
                detail=f"Test suite complete: {summary}",
                metadata=test_results)
        except Exception as exc:
            logger.warning("Review: test suite failed: %s", exc)
            test_results = {"summary": f"Error: {exc}", "error": str(exc)}

    progress_tracker.add_step(eid, "review", "thinking",
        detail="Evaluating completeness, correctness, code quality, and test coverage")

    files_created = state.get("files_created", [])
    files_modified = state.get("files_modified", [])
    commands_executed = state.get("commands_executed", [])
    has_meaningful_changes = _has_meaningful_file_changes(files_created, files_modified)
    if not has_meaningful_changes and workspace_path:
        has_meaningful_changes = _has_meaningful_workspace_files(workspace_path)
    if not has_meaningful_changes:
        feedback = (
            "No meaningful implementation changes detected. "
            "Please create/modify actual project files (not only housekeeping files like .gitignore) before delivery."
        )
        progress_tracker.add_step(
            eid,
            "review",
            "done",
            detail="Review failed: no meaningful implementation changes detected",
            metadata={"score": 0, "passed": False},
        )
        return {
            "phase": "review",
            "review_score": 0,
            "review_passed": False,
            "review_feedback": feedback,
            "test_results": test_results,
            "attempt_count": state.get("attempt_count", 0) + 1,
            "total_prompt_tokens": state.get("total_prompt_tokens", 0),
            "total_completion_tokens": state.get("total_completion_tokens", 0),
        }

    agent = ReviewAgent()
    # Inject test results into the state so ReviewAgent can see them
    state_for_agent = dict(state)
    state_for_agent["test_results"] = test_results
    
    result = await agent.run(state_for_agent)

    score = result.get("score", 0)
    passed = result.get("passed", False)
    feedback = result.get("feedback", "")
    next_attempt = int(result.get("attempt_count", state.get("attempt_count", 0) + 1))

    if passed:
        detail = f"Quality score: {score}/100 — looking great, ready for delivery!"
    else:
        detail = f"Quality score: {score}/100 — found some improvements to make. Going back to refine."

    # Git commit after review
    workspace_path = state.get("workspace_path")
    if workspace_path and passed:
        progress_tracker.add_step(eid, "review", "committing",
            detail="Committing reviewed code to version control")
        git = GitHelper(workspace_path)
        await git.add_commit_push(
            f"Phase: Review Complete - Quality score: {score}/100"
        )

    progress_tracker.add_step(eid, "review", "done", detail=detail,
        metadata={"score": score, "passed": passed, "feedback": feedback[:200]})

    return {
        "phase": "review",
        "review_score": score,
        "review_passed": passed,
        "review_feedback": feedback,
        "attempt_count": next_attempt,
        "test_results": test_results,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def deployment_node(state: TaskState) -> dict[str, Any]:
    """Create GitHub repo and deploy to Vercel.
    
    This is a deterministic node — no LLM calls. It runs deployment tools
    programmatically and collects results. Failures are non-blocking.
    """
    from app.tools.deployment import (
        create_github_repo,
        deploy_to_vercel,
    )

    eid = _eid(state)
    workspace_path = state.get("workspace_path", "")
    task_data = state.get("task_data", {})
    task_type = state.get("task_type", "general")
    execution_id = state.get("execution_id", 0)

    progress_tracker.add_step(eid, "deployment", "start",
        detail="Running the deployment pipeline: GitHub, Vercel")

    result: dict[str, Any] = {
        "phase": "deployment",
        "github_repo_url": None,
        "vercel_preview_url": None,
        "vercel_claim_url": None,
        "test_results": state.get("test_results", {}), # pass it forward so lifecycle formatting uses it
        "deployment_passed": True,
        "deployment_errors": [],
    }

    # 2. Create GitHub repo (MANDATORY — all deliveries must be on GitHub)
    progress_tracker.add_step(eid, "deployment", "github",
        detail="Creating GitHub repository and pushing code")

    import uuid as _uuid

    task_title = task_data.get("title", "delivery")
    # Slugify the title
    title_slug = task_title.lower()[:40]
    title_slug = title_slug.replace(" ", "-")
    title_slug = "".join(c for c in title_slug if c.isalnum() or c == "-")
    title_slug = title_slug.strip("-")

    # UUID suffix prevents name conflicts when tasks have similar titles
    uuid_suffix = _uuid.uuid4().hex[:8]
    repo_name = f"{settings.GITHUB_REPO_PREFIX}-{execution_id}-{title_slug}-{uuid_suffix}"
    description = f"Oriexa delivery for: {task_title}"

    try:
        if not settings.GITHUB_TOKEN:
            raise ValueError("GITHUB_TOKEN is not configured — add it to .env to enable GitHub deployment")
        gh_result = await create_github_repo(
            repo_name=repo_name,
            description=description,
            workspace_path=workspace_path,
        )
        if gh_result.get("success"):
            result["github_repo_url"] = gh_result["repo_url"]
            progress_tracker.add_step(eid, "deployment", "github",
                detail=f"Repository created: {gh_result['repo_url']}")

            # Verify repo actually has code (not empty)
            try:
                repo_url_path = gh_result["repo_url"].replace("https://github.com/", "")
                async with httpx.AsyncClient(timeout=15.0) as client:
                    verify_resp = await client.get(
                        f"https://api.github.com/repos/{repo_url_path}/commits?per_page=1",
                        headers={
                            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                            "Accept": "application/vnd.github+json",
                        },
                    )
                    if verify_resp.status_code == 200:
                        commits = verify_resp.json()
                        if not commits:
                            logger.warning("Repo %s exists but has NO commits — retrying push", repo_name)
                            progress_tracker.add_step(eid, "deployment", "github",
                                detail="Repo exists but empty — retrying push...")
                            # Retry push
                            from app.tools.deployment import create_github_repo as _retry_gh
                            await _retry_gh(repo_name, description, workspace_path)
                        else:
                            logger.info("Verified: repo %s has %d commit(s)", repo_name, len(commits))
                    elif verify_resp.status_code == 409:
                        # 409 = empty repo, push again
                        logger.warning("Repo %s is empty (409) — retrying push", repo_name)
                        from app.sandbox.executor import SandboxExecutor
                        executor = SandboxExecutor(timeout=30)
                        await executor.execute("git push -u origin main --force", cwd=workspace_path)
            except Exception as verify_exc:
                logger.warning("Post-push verification failed (non-blocking): %s", verify_exc)
        else:
            error_msg = gh_result.get("error", "unknown error")
            logger.error("GitHub repo creation failed: %s", error_msg)
            result["deployment_errors"].append(f"github: {error_msg}")
            progress_tracker.add_step(eid, "deployment", "github",
                detail=f"GitHub repo creation FAILED: {error_msg}")
    except Exception as exc:
        logger.error("GitHub repo creation error: %s", exc)
        result["deployment_errors"].append(f"github: {exc}")
        progress_tracker.add_step(eid, "deployment", "github",
            detail=f"GitHub error: {exc}")

    # 3. Deploy to Vercel (MANDATORY for all coding tasks)
    progress_tracker.add_step(eid, "deployment", "vercel",
        detail="Deploying to Vercel for live preview")

    try:
        if not settings.VERCEL_TOKEN and not settings.VERCEL_DEPLOY_ENDPOINT:
            raise ValueError("Neither VERCEL_TOKEN nor VERCEL_DEPLOY_ENDPOINT is configured — add one to .env")
        vercel_result = await deploy_to_vercel(workspace_path)
        if vercel_result.get("success"):
            result["vercel_preview_url"] = vercel_result.get("preview_url")
            result["vercel_claim_url"] = vercel_result.get("claim_url")
            progress_tracker.add_step(eid, "deployment", "vercel",
                detail=f"Deployed! Preview: {vercel_result.get('preview_url')}")
        else:
            error_msg = vercel_result.get("error", "unknown error")
            if "No deployable framework" in error_msg:
                progress_tracker.add_step(eid, "deployment", "vercel",
                    detail="No deployable framework detected — skipping Vercel for this project type")
            else:
                logger.warning("Vercel deploy failed (non-fatal): %s", error_msg)
                progress_tracker.add_step(eid, "deployment", "vercel",
                    detail=f"Vercel deploy failed (Non-fatal, continuing): {error_msg}")
    except Exception as exc:
        logger.warning("Vercel deployment error (non-fatal): %s", exc)
        progress_tracker.add_step(eid, "deployment", "vercel",
            detail=f"Vercel error (Non-fatal, continuing): {exc}")

    if not result["github_repo_url"]:
        result["deployment_errors"].append("github: missing repository URL")
    if task_type == "frontend" and not result["vercel_preview_url"]:
        result["deployment_errors"].append("vercel: missing preview URL for frontend task")
    result["deployment_errors"] = list(dict.fromkeys(result["deployment_errors"]))
    if result["deployment_errors"]:
        result["deployment_passed"] = False

    # 4. Final git commit with deployment metadata
    if workspace_path:
        git = GitHelper(workspace_path)
        commit_parts = ["Phase: Deployment Complete"]
        if result["github_repo_url"]:
            commit_parts.append(f"Repo: {result['github_repo_url']}")
        if result["vercel_preview_url"]:
            commit_parts.append(f"Preview: {result['vercel_preview_url']}")
        test_summary = result.get("test_results", {}).get("summary", "")
        if test_summary:
            commit_parts.append(f"Tests: {test_summary}")
        await git.add_commit_push(" | ".join(commit_parts))

    # Build summary for progress
    parts = []
    if result["github_repo_url"]:
        parts.append("GitHub repo created")
    if result["vercel_preview_url"]:
        parts.append("Vercel preview deployed")
    test_summary = result.get("test_results", {}).get("summary", "")
    if test_summary:
        parts.append(f"Tests: {test_summary}")
    detail = ". ".join(parts) if parts else "Deployment pipeline complete."
    if not result["deployment_passed"]:
        detail += " Deployment failed checks: " + "; ".join(result["deployment_errors"])

    progress_tracker.add_step(eid, "deployment", "done", detail=detail,
        metadata={
            "github_repo_url": result["github_repo_url"],
            "vercel_preview_url": result["vercel_preview_url"],
            "deployment_passed": result["deployment_passed"],
        })

    return result


async def delivery_node(state: TaskState) -> dict[str, Any]:
    """Submit the deliverable to Oriexa."""
    from app.orchestrator.lifecycle import deliver_task

    eid = _eid(state)
    progress_tracker.add_step(eid, "delivery", "start",
        detail="Everything passed review — packaging it all up with a bow on top")
    progress_tracker.add_step(eid, "delivery", "submitting",
        detail="Uploading the deliverable with a complete summary of what was built")

    result = await deliver_task(state)

    if "error" in result and result["error"]:
        progress_tracker.add_step(eid, "delivery", "done",
            detail=f"Hit a snag during delivery: {result['error']}")
    else:
        files_created = state.get("files_created", [])

        # Final Git commit after successful delivery
        workspace_path = state.get("workspace_path")
        if workspace_path:
            progress_tracker.add_step(eid, "delivery", "finalizing",
                detail="Creating final commit with delivery metadata")
            git = GitHelper(workspace_path)
            await git.add_commit_push(
                f"Phase: Delivery Complete - Task delivered with {len(files_created)} file(s)"
            )

        progress_tracker.add_step(eid, "delivery", "done",
            detail=f"Successfully delivered! {len(files_created)} file(s) included in the final package.",
            metadata={"files_count": len(files_created)})

    return {
        "phase": "delivery",
        **result,
    }


async def failed_node(state: TaskState) -> dict[str, Any]:
    """Handle task failure."""
    from app.orchestrator.lifecycle import handle_failure

    eid = _eid(state)
    error = state.get("error", "")
    feedback = state.get("review_feedback", "")

    progress_tracker.add_step(eid, "failed", "start",
        detail=f"Unfortunately this one didn't make it across the finish line. {error or feedback or 'Max attempts reached.'}")
    progress_tracker.add_step(eid, "failed", "done",
        detail="Issue logged for review. The workspace files are preserved for inspection.",
        metadata={"error": error, "feedback": feedback})

    result = await handle_failure(state)
    return {
        "phase": "failed",
        **result,
    }


# ---------------------------------------------------------------------------
# Edge routing functions
# ---------------------------------------------------------------------------

def route_after_triage(state: TaskState) -> str:
    # Product decision: after a claim is accepted, do not block execution
    # with additional clarification. Keep moving and handle ambiguity in-chat.
    if bool(state.get("disable_post_claim_clarification", True)):
        return "planning"

    # Legacy path (kept for optional future toggle)
    clarity = state.get("clarity_score", 0.5)
    if state.get("needs_clarification", False) and clarity < 0.85:
        return "clarification"
    return "planning"


def route_after_clarification(state: TaskState) -> str:
    if state.get("waiting_for_response", False):
        return "wait_for_response"
    return "planning"


def route_after_planning(state: TaskState) -> str:
    complexity = state.get("complexity", "medium")
    budget = state.get("task_data", {}).get("budget_credits", 0)
    if complexity == "high" or budget > 500:
        return "complex_execution"
    return "execution"


def route_after_review(state: TaskState) -> str:
    if state.get("review_passed", False):
        return "deployment"
    attempt = int(state.get("attempt_count", 0))
    complexity = state.get("complexity", "medium")
    default_max = 2 if complexity == "low" else 3
    max_attempts = int(state.get("max_attempts", default_max))
    if attempt >= max_attempts:
        return "failed"

    if state.get("replan_requested", False):
        return "planning"

    budget = int(state.get("task_data", {}).get("budget_credits", 0))
    score = int(state.get("review_score", 0))
    # Escalate to stronger execution when quality is poor or retries are mounting.
    if complexity == "high" or budget > 500 or score <= 55 or attempt >= 2:
        return "complex_execution"
    if "no meaningful implementation changes detected" in str(state.get("review_feedback", "")).lower():
        return "complex_execution"
    return "execution"

def route_after_deployment(state: TaskState) -> str:
    if state.get("deployment_passed", True):
        return "delivery"
    return "failed"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_supervisor_graph() -> StateGraph:
    """Build and compile the LangGraph supervisor graph."""

    graph = StateGraph(TaskState)

    graph.add_node("triage", triage_node)
    graph.add_node("clarification", clarification_node)
    graph.add_node("wait_for_response", wait_for_response_node)
    graph.add_node("planning", planning_node)
    graph.add_node("execution", execution_node)
    graph.add_node("complex_execution", complex_execution_node)
    graph.add_node("review", review_node)
    graph.add_node("deployment", deployment_node)
    graph.add_node("delivery", delivery_node)
    graph.add_node("failed", failed_node)

    graph.set_entry_point("triage")

    graph.add_conditional_edges("triage", route_after_triage, {
        "clarification": "clarification",
        "planning": "planning",
    })
    graph.add_conditional_edges("clarification", route_after_clarification, {
        "wait_for_response": "wait_for_response",
        "planning": "planning",
    })
    graph.add_edge("wait_for_response", "planning")
    graph.add_conditional_edges("planning", route_after_planning, {
        "execution": "execution",
        "complex_execution": "complex_execution",
    })
    graph.add_edge("execution", "review")
    graph.add_edge("complex_execution", "review")
    graph.add_conditional_edges("review", route_after_review, {
        "deployment": "deployment",
        "execution": "execution",
        "complex_execution": "complex_execution",
        "failed": "failed",
    })
    graph.add_conditional_edges("deployment", route_after_deployment, {
        "delivery": "delivery",
        "failed": "failed",
    })
    graph.add_edge("delivery", END)
    graph.add_edge("failed", END)

    return graph
