"""
Node: fetch_deliverable

Fetches the submitted deliverable from the Oriexa API.
"""

from __future__ import annotations
import os
import httpx
from app.agents.user_reviewer.state import ReviewerState


def fetch_deliverable(state: ReviewerState) -> dict:
    """Fetch the deliverable content from the API."""
    if state.get("error") and state.get("skip_review"):
        return {}

    task_id = state["task_id"]
    deliverable_id = state["deliverable_id"]
    base_url = os.environ["ORIEXA_BASE_URL"]
    api_key = os.environ["ORIEXA_REVIEWER_API_KEY"]

    # Fetch specific deliverable — get task details which includes deliverables
    # We poll the task's deliverable via the tasks/:id endpoint
    try:
        resp = httpx.get(
            f"{base_url}/api/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"Failed to fetch deliverable: {exc}"}

    if not data.get("ok"):
        return {"error": "Could not fetch task to get deliverable details"}

    task = data["data"]
    deliverables = task.get("deliverables", [])

    # Find the matching deliverable
    target = None
    for d in deliverables:
        if d["id"] == deliverable_id:
            target = d
            break

    # If not found in task details, try the deliverables endpoint directly
    if not target:
        # Fallback: use the deliverable_id directly from the task's current deliverable
        # The reviewer knows the task is "delivered" so the latest deliverable is what matters
        if deliverables:
            target = deliverables[-1]  # Use most recent

    if not target:
        return {"error": f"Deliverable {deliverable_id} not found on task {task_id}"}

    return {
        "deliverable_content": target["content"],
        "deliverable_revision_number": target.get("revision_number", 1),
        "deliverable_agent_id": target.get("agent_id", state.get("claimed_by_agent_id", 0)),
        "deliverable_submitted_at": target.get("submitted_at", ""),
    }
