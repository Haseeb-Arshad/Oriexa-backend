"""LangGraph supervisor graph — orchestrates agents through the task pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from langgraph.graph import END, StateGraph

from app.config import settings
from app.orchestrator.progress import progress_tracker
from app.orchestrator.state import TaskState
from app.orchestrator.git_helper import GitHelper

logger = logging.getLogger(__name__)


def _eid(state: TaskState) -> int:
    """Extract execution_id from state."""
    return state.get("execution_id", 0)


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
            data = body.get("data", body)
            return data if isinstance(data, list) else []
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

    return {
        "phase": "triage",
        "clarity_score": result.get("clarity_score", 0.5),
        "complexity": complexity,
        "needs_clarification": needs_clarification,
        "triage_reasoning": reasoning,
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
    question_summary = result.get("question_summary", "")

    if clarification_needed and message_id:
        progress_tracker.add_step(eid, "clarification", "done",
            detail=f"Posted question to the poster — {question_summary}",
            metadata={"question_count": len(questions), "message_id": message_id})
    else:
        progress_tracker.add_step(eid, "clarification", "done",
            detail="Task is clear enough to proceed directly to planning",
            metadata={"question_count": 0})

    return {
        "phase": "clarification",
        "clarification_questions": questions,
        "clarification_message_sent": clarification_needed and message_id is not None,
        "clarification_message_id": message_id,
        "waiting_for_response": clarification_needed and message_id is not None,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def wait_for_response_node(state: TaskState) -> dict[str, Any]:
    """Poll for the poster's response to the clarification question.

    Checks every 15 seconds for up to 15 minutes. Looks for:
    1. structured_data.responded_at on the original question message
    2. A poster reply message with parent_id matching the question
    """
    message_id = state.get("clarification_message_id")
    task_id = state.get("oriexa_task_id") or state.get("task_data", {}).get("id")
    execution_id = state.get("execution_id", 0)

    # Set CLARIFYING status so UI can show it
    await _update_execution_status(execution_id, "clarifying")

    progress_tracker.add_step(execution_id, "clarification", "waiting",
        detail="Waiting for the poster to respond to the question...")

    if not task_id:
        logger.warning("wait_for_response_node: no task_id in state")
        return {"waiting_for_response": False, "clarification_response": None, "phase": "planning"}

    # Poll every 15s for up to 15 minutes (60 iterations)
    max_polls = 60
    poll_interval = 15

    for poll in range(max_polls):
        messages = await _fetch_messages_for_task(task_id)

        if message_id:
            # Check if the original question has a responded_at in structured_data
            question_msg = next((m for m in messages if m.get("id") == message_id), None)
            if question_msg:
                sd = question_msg.get("structured_data") or {}
                if sd.get("responded_at"):
                    response = sd.get("response", "")
                    progress_tracker.add_step(execution_id, "clarification", "responded",
                        detail=f"Poster responded: {response[:100]}...")
                    return {
                        "waiting_for_response": False,
                        "clarification_response": response,
                        "phase": "planning",
                    }

            # Check for a poster reply with parent_id matching our question
            reply = next(
                (m for m in messages
                 if m.get("parent_id") == message_id
                 and m.get("sender_type") == "poster"),
                None,
            )
            if reply:
                response = reply.get("content", "")
                progress_tracker.add_step(execution_id, "clarification", "responded",
                    detail=f"Poster responded: {response[:100]}...")
                return {
                    "waiting_for_response": False,
                    "clarification_response": response,
                    "phase": "planning",
                }

        # Also check for any poster message posted after the question
        if messages and message_id:
            poster_msgs = [
                m for m in messages
                if m.get("sender_type") == "poster"
                and isinstance(m.get("id"), int)
                and m["id"] > message_id
            ]
            if poster_msgs:
                response = poster_msgs[-1].get("content", "")
                progress_tracker.add_step(execution_id, "clarification", "responded",
                    detail=f"Poster responded: {response[:100]}...")
                return {
                    "waiting_for_response": False,
                    "clarification_response": response,
                    "phase": "planning",
                }

        await asyncio.sleep(poll_interval)

    # Timeout — proceed without response
    progress_tracker.add_step(execution_id, "clarification", "timeout",
        detail="No response received after 15 minutes — proceeding with planning based on available info")

    return {"waiting_for_response": False, "clarification_response": None, "phase": "planning"}


async def planning_node(state: TaskState) -> dict[str, Any]:
    """Run the PlanningAgent to decompose the task into subtasks."""
    from app.agents.planning import PlanningAgent

    eid = _eid(state)
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
    planning_state = dict(state)
    if clarification_response:
        original_desc = planning_state.get("task_data", {}).get("description", "")
        planning_state.setdefault("task_data", {})
        planning_state["task_data"] = dict(planning_state["task_data"])
        planning_state["task_data"]["description"] = (
            f"Poster clarified: {clarification_response}\n\n{original_desc}"
        )

    agent = PlanningAgent()
    result = await agent.run(planning_state)

    plan = result.get("plan", [])
    subtask_titles = [s.get("title", "Step") for s in plan]

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
        "current_subtask_index": 0,
        "subtask_results": [],
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def execution_node(state: TaskState) -> dict[str, Any]:
    """Run the ExecutionAgent to execute all subtasks."""
    from app.agents.execution import ExecutionAgent

    eid = _eid(state)
    plan = state.get("plan", [])
    progress_tracker.add_step(eid, "execution", "start",
        detail=f"Executing {len(plan)} subtask(s) — writing code, running commands, building it out")

    progress_tracker.add_step(eid, "execution", "writing",
        detail="Fingers on keyboard — creating files, writing implementations, wiring things together")

    agent = ExecutionAgent()
    result = await agent.run(state)

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
        "subtask_results": result.get("subtask_results", []),
        "files_created": files_created,
        "files_modified": files_modified,
        "commands_executed": commands,
        "deliverable_content": result.get("deliverable_content", ""),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def complex_execution_node(state: TaskState) -> dict[str, Any]:
    """Run the ComplexTaskAgent for high-complexity or high-budget tasks."""
    from app.agents.complex_task import ComplexTaskAgent

    eid = _eid(state)
    plan = state.get("plan", [])
    progress_tracker.add_step(eid, "complex_execution", "start",
        detail=f"This one needs the full treatment — engaging deep reasoning for {len(plan)} subtask(s)")

    progress_tracker.add_step(eid, "complex_execution", "analyzing",
        detail="Thinking through edge cases, architecture patterns, and the cleanest path forward")

    progress_tracker.add_step(eid, "complex_execution", "writing",
        detail="Building the implementation with careful attention to every detail")

    agent = ComplexTaskAgent()
    result = await agent.run(state)

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
        "subtask_results": result.get("subtask_results", []),
        "files_created": files_created,
        "files_modified": files_modified,
        "commands_executed": result.get("commands_executed", []),
        "deliverable_content": result.get("deliverable_content", ""),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def review_node(state: TaskState) -> dict[str, Any]:
    """Run the ReviewAgent to validate the deliverable."""
    from app.agents.review import ReviewAgent

    eid = _eid(state)
    progress_tracker.add_step(eid, "review", "start",
        detail="Putting on the reviewer hat — checking everything against the original requirements")
    progress_tracker.add_step(eid, "review", "thinking",
        detail="Evaluating completeness, correctness, code quality, and test coverage")

    agent = ReviewAgent()
    result = await agent.run(state)

    score = result.get("score", 0)
    passed = result.get("passed", False)
    feedback = result.get("feedback", "")

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
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


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
    if state.get("needs_clarification", False):
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
        return "delivery"
    attempt = state.get("attempt_count", 0)
    max_attempts = state.get("max_attempts", 3)
    if attempt < max_attempts:
        return "planning"
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
        "delivery": "delivery",
        "planning": "planning",
        "failed": "failed",
    })
    graph.add_edge("delivery", END)
    graph.add_edge("failed", END)

    return graph
