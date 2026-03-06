"""
Node: read_task

Fetches task details from the Oriexa API and validates that
the task is eligible for automated review.
"""

from __future__ import annotations
import os
import httpx
from app.agents.user_reviewer.state import ReviewerState


def read_task(state: ReviewerState) -> dict:
    """Fetch task details and validate auto-review eligibility."""
    task_id = state["task_id"]
    base_url = os.environ["ORIEXA_BASE_URL"]
    api_key = os.environ["ORIEXA_REVIEWER_API_KEY"]

    try:
        resp = httpx.get(
            f"{base_url}/api/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"Failed to fetch task {task_id}: {exc}"}

    if not data.get("ok"):
        err = data.get("error", {})
        return {"error": f"API error fetching task {task_id}: {err.get('message')}"}

    task = data["data"]

    if not task.get("auto_review_enabled"):
        return {
            "error": f"Task {task_id} does not have auto_review_enabled",
            "skip_review": True,
        }

    if task.get("status") not in ("delivered",):
        return {
            "error": f"Task {task_id} is in status '{task['status']}', not 'delivered'",
            "skip_review": True,
        }

    return {
        "task_title": task["title"],
        "task_description": task["description"],
        "task_requirements": task.get("requirements"),
        "task_budget": task["budget_credits"],
        "task_status": task["status"],
        "task_auto_review_enabled": True,
        "claimed_by_agent_id": task.get("claimed_by_agent_id"),
    }
