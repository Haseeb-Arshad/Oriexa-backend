"""Delivery and failure handling for orchestrator task lifecycle."""

from __future__ import annotations

import logging
from typing import Any

from app.oriexa_client.client import OriexaClient

logger = logging.getLogger(__name__)


async def deliver_task(state: dict[str, Any]) -> dict[str, Any]:
    """Submit the deliverable to Oriexa and update execution status."""
    client = OriexaClient()
    task_id = state.get("oriexa_task_id")
    content = state.get("deliverable_content", "")

    if not task_id or not content:
        logger.error("Cannot deliver: missing task_id or content")
        await client.close()
        return {"error": "Missing task_id or deliverable content"}

    try:
        # Build a comprehensive deliverable
        deliverable_parts = [content]

        # Append deployment URLs if available
        github_url = state.get("github_repo_url")
        vercel_preview = state.get("vercel_preview_url")
        vercel_claim = state.get("vercel_claim_url")
        test_results = state.get("test_results", {})

        if github_url or vercel_preview:
            deliverable_parts.append("\n\n---\n## Deployment")
            if github_url:
                deliverable_parts.append(f"**GitHub Repository:** {github_url}")
            if vercel_preview:
                deliverable_parts.append(f"**Live Preview:** {vercel_preview}")
            if vercel_claim:
                deliverable_parts.append(f"**Claim Deployment:** {vercel_claim}")

        # Append test results summary
        if test_results and test_results.get("summary"):
            deliverable_parts.append("\n\n---\n## Test Results")
            deliverable_parts.append(f"**Summary:** {test_results['summary']}")
            for stage in ("lint", "typecheck", "tests", "build"):
                key = f"{stage}_passed"
                if test_results.get(key) is not None:
                    status = "Passed" if test_results[key] else "Failed"
                    deliverable_parts.append(f"- {stage.title()}: {status}")

        # Append file manifest if files were created/modified
        files_created = state.get("files_created", [])
        files_modified = state.get("files_modified", [])
        if files_created or files_modified:
            deliverable_parts.append("\n\n---\n## Files Changed")
            if files_created:
                deliverable_parts.append("### Created")
                for f in files_created:
                    deliverable_parts.append(f"- `{f}`")
            if files_modified:
                deliverable_parts.append("### Modified")
                for f in files_modified:
                    deliverable_parts.append(f"- `{f}`")

        full_content = "\n".join(deliverable_parts)

        result = await client.submit_deliverable(task_id, full_content)
        if result:
            logger.info("Deliverable submitted for task %s", task_id)
            return {"phase": "delivery"}
        else:
            logger.error("Failed to submit deliverable for task %s", task_id)
            return {"error": "Failed to submit deliverable via API"}
    except Exception as exc:
        logger.exception("Delivery failed for task %s: %s", task_id, exc)
        return {"error": str(exc)}
    finally:
        await client.close()


async def handle_failure(state: dict[str, Any]) -> dict[str, Any]:
    """Handle a failed task execution — log error and notify if possible."""
    task_id = state.get("oriexa_task_id")
    error = state.get("error", "Unknown error")
    review_feedback = state.get("review_feedback", "")

    logger.error(
        "Task %s failed after %d attempts. Error: %s. Review feedback: %s",
        task_id,
        state.get("attempt_count", 0),
        error,
        review_feedback,
    )

    return {
        "phase": "failed",
        "error": error or review_feedback or "Max attempts exceeded",
    }
