"""LangGraph supervisor graph — orchestrates agents through the task pipeline."""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from app.orchestrator.state import TaskState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node functions — each invokes the corresponding agent
# ---------------------------------------------------------------------------

async def triage_node(state: TaskState) -> dict[str, Any]:
    """Run the TriageAgent to assess task clarity and complexity."""
    from app.agents.triage import TriageAgent

    agent = TriageAgent()
    result = await agent.run(state)
    return {
        "phase": "triage",
        "clarity_score": result.get("clarity_score", 0.5),
        "complexity": result.get("complexity", "medium"),
        "needs_clarification": result.get("needs_clarification", False),
        "triage_reasoning": result.get("reasoning", ""),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def clarification_node(state: TaskState) -> dict[str, Any]:
    """Run the ClarificationAgent to generate questions for the poster."""
    from app.agents.clarification import ClarificationAgent

    agent = ClarificationAgent()
    result = await agent.run(state)
    return {
        "phase": "clarification",
        "clarification_questions": result.get("questions", []),
        "clarification_message_sent": True,
        "waiting_for_response": True,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def wait_for_response_node(state: TaskState) -> dict[str, Any]:
    """Placeholder node — graph pauses here via interrupt_before.

    The TaskPickerDaemon will resume the graph when a poster response is detected.
    """
    return {"waiting_for_response": False, "phase": "planning"}


async def planning_node(state: TaskState) -> dict[str, Any]:
    """Run the PlanningAgent to decompose the task into subtasks."""
    from app.agents.planning import PlanningAgent

    agent = PlanningAgent()
    result = await agent.run(state)
    return {
        "phase": "planning",
        "plan": result.get("plan", []),
        "current_subtask_index": 0,
        "subtask_results": [],
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def execution_node(state: TaskState) -> dict[str, Any]:
    """Run the ExecutionAgent to execute all subtasks."""
    from app.agents.execution import ExecutionAgent

    agent = ExecutionAgent()
    result = await agent.run(state)
    return {
        "phase": "execution",
        "subtask_results": result.get("subtask_results", []),
        "files_created": result.get("files_created", []),
        "files_modified": result.get("files_modified", []),
        "commands_executed": result.get("commands_executed", []),
        "deliverable_content": result.get("deliverable_content", ""),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def complex_execution_node(state: TaskState) -> dict[str, Any]:
    """Run the ComplexTaskAgent for high-complexity or high-budget tasks."""
    from app.agents.complex_task import ComplexTaskAgent

    agent = ComplexTaskAgent()
    result = await agent.run(state)
    return {
        "phase": "execution",
        "subtask_results": result.get("subtask_results", []),
        "files_created": result.get("files_created", []),
        "files_modified": result.get("files_modified", []),
        "commands_executed": result.get("commands_executed", []),
        "deliverable_content": result.get("deliverable_content", ""),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def review_node(state: TaskState) -> dict[str, Any]:
    """Run the ReviewAgent to validate the deliverable."""
    from app.agents.review import ReviewAgent

    agent = ReviewAgent()
    result = await agent.run(state)
    return {
        "phase": "review",
        "review_score": result.get("score", 0),
        "review_passed": result.get("passed", False),
        "review_feedback": result.get("feedback", ""),
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + result.get("prompt_tokens", 0),
        "total_completion_tokens": state.get("total_completion_tokens", 0) + result.get("completion_tokens", 0),
    }


async def delivery_node(state: TaskState) -> dict[str, Any]:
    """Submit the deliverable to Oriexa."""
    from app.orchestrator.lifecycle import deliver_task

    result = await deliver_task(state)
    return {
        "phase": "delivery",
        **result,
    }


async def failed_node(state: TaskState) -> dict[str, Any]:
    """Handle task failure."""
    from app.orchestrator.lifecycle import handle_failure

    result = await handle_failure(state)
    return {
        "phase": "failed",
        **result,
    }


# ---------------------------------------------------------------------------
# Edge routing functions
# ---------------------------------------------------------------------------

def route_after_triage(state: TaskState) -> str:
    """After triage, decide whether to clarify or plan."""
    if state.get("needs_clarification", False):
        return "clarification"
    return "planning"


def route_after_clarification(state: TaskState) -> str:
    """After clarification, either wait for response or proceed to planning."""
    if state.get("waiting_for_response", False):
        return "wait_for_response"
    return "planning"


def route_after_planning(state: TaskState) -> str:
    """After planning, route to execution or complex execution based on complexity."""
    complexity = state.get("complexity", "medium")
    budget = state.get("task_data", {}).get("budget_credits", 0)
    if complexity == "high" or budget > 500:
        return "complex_execution"
    return "execution"


def route_after_review(state: TaskState) -> str:
    """After review, either deliver or retry planning."""
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

    # Add nodes
    graph.add_node("triage", triage_node)
    graph.add_node("clarification", clarification_node)
    graph.add_node("wait_for_response", wait_for_response_node)
    graph.add_node("planning", planning_node)
    graph.add_node("execution", execution_node)
    graph.add_node("complex_execution", complex_execution_node)
    graph.add_node("review", review_node)
    graph.add_node("delivery", delivery_node)
    graph.add_node("failed", failed_node)

    # Set entry point
    graph.set_entry_point("triage")

    # Conditional edges
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
