"""
ReviewerState — the shared state object flowing through the LangGraph graph.

Each node reads from and writes back to this TypedDict.
"""

from __future__ import annotations
from typing import Any, Optional
from typing_extensions import TypedDict


class ReviewerState(TypedDict, total=False):
    # ── Inputs ────────────────────────────────────────────────────────────────
    task_id: int
    deliverable_id: int

    # ── Task data (populated by read_task node) ───────────────────────────────
    task_title: str
    task_description: str
    task_requirements: Optional[str]
    task_budget: int
    task_status: str
    task_auto_review_enabled: bool
    claimed_by_agent_id: Optional[int]

    # ── Deliverable data (populated by fetch_deliverable node) ────────────────
    deliverable_content: str
    deliverable_revision_number: int
    deliverable_agent_id: int
    deliverable_submitted_at: str

    # ── LLM key resolution (populated by resolve_api_key node) ───────────────
    llm_api_key: Optional[str]
    llm_provider: Optional[str]  # "openrouter" | "openai" | "anthropic"
    llm_model: Optional[str]
    key_source: str  # "poster" | "freelancer" | "none"
    poster_reviews_used: int
    poster_max_reviews: Optional[int]

    # ── Review result (populated by analyze_content + generate_verdict nodes) ─
    verdict: Optional[str]  # "pass" | "fail"
    review_feedback: Optional[str]
    review_scores: dict[str, Any]

    # ── Errors ────────────────────────────────────────────────────────────────
    error: Optional[str]
    skip_review: bool  # True when no LLM key available

    # ── URL check (populated by browse_url node) ───────────────────────────────
    url_check_results: dict[str, Any]  # {url: {status_code, reachable, error?}}
