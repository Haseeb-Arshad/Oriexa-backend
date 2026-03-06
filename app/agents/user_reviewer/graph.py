"""
Oriexa Reviewer Agent — LangGraph Graph Definition

Graph flow:
    read_task → fetch_deliverable → resolve_api_key → analyze_content → browse_url → post_review

Conditional routing:
    - After read_task: if skip_review or error → END
    - After fetch_deliverable: if error → END
    - After resolve_api_key: if skip_review → post_review (records skipped) → END
    - After analyze_content: if error → END
    - browse_url runs regardless (notes URL status for verdict context)
    - Always reaches post_review → END
"""

from __future__ import annotations
from langgraph.graph import StateGraph, END
from app.agents.user_reviewer.state import ReviewerState
from app.agents.user_reviewer.nodes.read_task import read_task
from app.agents.user_reviewer.nodes.fetch_deliverable import fetch_deliverable
from app.agents.user_reviewer.nodes.resolve_api_key import resolve_api_key
from app.agents.user_reviewer.nodes.analyze_content import analyze_content
from app.agents.user_reviewer.nodes.browse_url import browse_url
from app.agents.user_reviewer.nodes.post_review import post_review


def should_continue_after_read(state: ReviewerState) -> str:
    """After read_task: continue or end if task is not eligible."""
    if state.get("skip_review") or state.get("error"):
        return "end"
    return "continue"


def should_continue_after_fetch(state: ReviewerState) -> str:
    """After fetch_deliverable: continue or end on error."""
    if state.get("error"):
        return "end"
    return "continue"


def should_analyze_or_skip(state: ReviewerState) -> str:
    """After resolve_api_key: if no key, go directly to post_review to record skip."""
    if state.get("skip_review") or state.get("error"):
        return "post_review"
    return "analyze"


def should_post_or_end(state: ReviewerState) -> str:
    """After analyze_content: post review or end on error."""
    if state.get("error"):
        return "end"
    return "post_review"


def build_graph() -> StateGraph:
    """Build and compile the reviewer agent graph."""
    workflow = StateGraph(ReviewerState)

    # Add nodes
    workflow.add_node("read_task", read_task)
    workflow.add_node("fetch_deliverable", fetch_deliverable)
    workflow.add_node("resolve_api_key", resolve_api_key)
    workflow.add_node("analyze_content", analyze_content)
    workflow.add_node("browse_url", browse_url)
    workflow.add_node("post_review", post_review)

    # Entry point
    workflow.set_entry_point("read_task")

    # Edges with conditional routing
    workflow.add_conditional_edges(
        "read_task",
        should_continue_after_read,
        {"continue": "fetch_deliverable", "end": END},
    )

    workflow.add_conditional_edges(
        "fetch_deliverable",
        should_continue_after_fetch,
        {"continue": "resolve_api_key", "end": END},
    )

    workflow.add_conditional_edges(
        "resolve_api_key",
        should_analyze_or_skip,
        {"analyze": "analyze_content", "post_review": "post_review"},
    )

    workflow.add_conditional_edges(
        "analyze_content",
        should_post_or_end,
        {"browse_url": "browse_url", "end": END},
    )

    # browse_url always proceeds to post_review
    workflow.add_edge("browse_url", "post_review")
    workflow.add_edge("post_review", END)

    return workflow.compile()


# Singleton compiled graph
app = build_graph()
