"""LangGraph state definition for the supervisor graph."""

from __future__ import annotations

from typing import Any, TypedDict


class SubtaskPlan(TypedDict):
    title: str
    description: str
    depends_on: list[int]


class SubtaskResult(TypedDict):
    index: int
    title: str
    status: str  # "completed" | "failed" | "skipped"
    result: str
    files_changed: list[str]


class TaskState(TypedDict, total=False):
    """State that flows through the LangGraph supervisor graph."""

    # Task identity
    execution_id: int
    oriexa_task_id: int

    # Task data snapshot from Oriexa API
    task_data: dict[str, Any]

    # Current phase
    phase: str  # "triage" | "clarification" | "planning" | "execution" | "review" | "delivery"

    # Triage results
    clarity_score: float
    complexity: str  # "low" | "medium" | "high"
    needs_clarification: bool
    triage_reasoning: str

    # Clarification
    clarification_questions: list[str]
    clarification_message_sent: bool
    clarification_response: str | None
    waiting_for_response: bool

    # Planning
    plan: list[SubtaskPlan]
    subtask_results: list[SubtaskResult]
    current_subtask_index: int

    # Workspace
    workspace_path: str

    # Execution tracking
    files_created: list[str]
    files_modified: list[str]
    commands_executed: list[dict[str, Any]]

    # Deliverable
    deliverable_content: str

    # Review
    review_score: int
    review_passed: bool
    review_feedback: str

    # Retry tracking
    attempt_count: int
    max_attempts: int

    # Token tracking
    total_prompt_tokens: int
    total_completion_tokens: int

    # Loop detection
    action_hashes: list[str]

    # Error tracking
    error: str | None

    # Messages exchanged with poster
    messages: list[dict[str, Any]]
