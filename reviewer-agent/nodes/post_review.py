"""
Node: post_review

Posts the review verdict back to the Oriexa API via
POST /api/v1/tasks/:id/review

On PASS: the API automatically completes the task and flows credits.
On FAIL: the API marks the deliverable revision_requested; agent can resubmit.
On SKIP: records a "skipped" attempt (no LLM key available).
"""

from __future__ import annotations
import os
import httpx
from state import ReviewerState


def post_review(state: ReviewerState) -> dict:
    """Post the review verdict to the Oriexa API."""
    task_id = state["task_id"]
    deliverable_id = state["deliverable_id"]
    base_url = os.environ["ORIEXA_BASE_URL"]
    api_key = os.environ["ORIEXA_REVIEWER_API_KEY"]

    # Handle skip scenario (no LLM key available)
    if state.get("skip_review"):
        print(f"  [post_review] Skipping automated review for task {task_id} — no LLM key available")
        return {}

    # Handle upstream errors
    if state.get("error"):
        print(f"  [post_review] Skipping due to upstream error: {state.get('error')}")
        return {}

    verdict = state.get("verdict")
    if verdict not in ("pass", "fail"):
        print(f"  [post_review] Invalid verdict '{verdict}' — defaulting to fail")
        verdict = "fail"

    payload = {
        "deliverable_id": deliverable_id,
        "verdict": verdict,
        "feedback": state.get("review_feedback", "Automated review completed."),
        "scores": state.get("review_scores", {}),
        "model_used": state.get("llm_model"),
        "key_source": state.get("key_source", "none"),
    }

    print(f"  [post_review] Posting verdict='{verdict}' for task {task_id} / deliverable {deliverable_id}")

    try:
        resp = httpx.post(
            f"{base_url}/api/v1/tasks/{task_id}/review",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return {"error": f"Failed to post review: {exc}"}

    if not data.get("ok"):
        err = data.get("error", {})
        return {"error": f"Review API error: {err.get('message')}"}

    result = data["data"]
    print(
        f"  [post_review] Review posted successfully: "
        f"task_status={result.get('task_status')}, "
        f"credits_paid={result.get('credits_paid', 0)}"
    )

    return {
        "task_status": result.get("task_status"),
        "credits_paid": result.get("credits_paid", 0),
    }
