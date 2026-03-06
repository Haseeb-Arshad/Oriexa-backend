"""Delivery and failure handling for orchestrator task lifecycle."""

from __future__ import annotations

import logging
from typing import Any

from app.oriexa_client.client import OriexaClient

logger = logging.getLogger(__name__)


async def deliver_task(state: dict[str, Any]) -> dict[str, Any]:
    """Submit a lean, structured deliverable to Oriexa.

    Instead of dumping raw source code into the API payload, this builds
    a compact markdown summary. The actual code lives in the workspace /
    Git repo — the deliverable just describes what was built.
    """
    client = OriexaClient()
    task_id = state.get("oriexa_task_id")
    summary = state.get("deliverable_content", "")  # Already a summary from the new agents

    if not task_id:
        logger.error("Cannot deliver: missing task_id")
        await client.close()
        return {"error": "Missing task_id"}

    try:
        # ── Build structured markdown deliverable ────────────────────
        sections: list[str] = []

        # 1. Summary (from the LLM — this is now a lean description, not raw code)
        if summary:
            sections.append("## Summary\n")
            sections.append(summary.strip())
        else:
            sections.append("## Summary\n")
            sections.append("Task completed successfully. See the repository for full implementation details.")

        # 2. Deployment links
        github_url = state.get("github_repo_url")
        vercel_preview = state.get("vercel_preview_url")
        vercel_claim = state.get("vercel_claim_url")

        if github_url or vercel_preview:
            sections.append("\n\n---\n## Deployment\n")
            if github_url:
                sections.append(f"- **GitHub Repository:** [{github_url}]({github_url})")
            if vercel_preview:
                sections.append(f"- **Live Preview:** [{vercel_preview}]({vercel_preview})")
            if vercel_claim:
                sections.append(f"- **Claim Deployment:** [{vercel_claim}]({vercel_claim})")

        # 3. Test results (compact)
        test_results = state.get("test_results", {})
        if test_results and test_results.get("summary"):
            sections.append("\n\n---\n## Test Results\n")
            sections.append(f"**Summary:** {test_results['summary']}")
            for stage in ("lint", "typecheck", "tests", "build"):
                key = f"{stage}_passed"
                if test_results.get(key) is not None:
                    icon = "✅" if test_results[key] else "❌"
                    sections.append(f"- {stage.title()}: {icon}")

        # 4. File manifest (capped at 50 to avoid bloat)
        files_created = state.get("files_created", [])
        files_modified = state.get("files_modified", [])
        total_files = len(files_created) + len(files_modified)

        if total_files > 0:
            sections.append(f"\n\n---\n## Files Changed ({total_files} total)\n")
            MAX_FILES_SHOWN = 50
            shown = 0

            if files_created:
                sections.append("### Created")
                for f in files_created[:MAX_FILES_SHOWN]:
                    sections.append(f"- `{f}`")
                    shown += 1

            if files_modified and shown < MAX_FILES_SHOWN:
                sections.append("### Modified")
                remaining = MAX_FILES_SHOWN - shown
                for f in files_modified[:remaining]:
                    sections.append(f"- `{f}`")
                    shown += 1

            if total_files > MAX_FILES_SHOWN:
                sections.append(f"\n*...and {total_files - MAX_FILES_SHOWN} more file(s)*")

        # 5. Token usage stats (compact metadata)
        prompt_tokens = state.get("total_prompt_tokens", 0)
        completion_tokens = state.get("total_completion_tokens", 0)
        if prompt_tokens or completion_tokens:
            sections.append(f"\n\n---\n*Token usage: {prompt_tokens:,} prompt + {completion_tokens:,} completion = {prompt_tokens + completion_tokens:,} total*")

        full_content = "\n".join(sections)

        # Safety truncation (should rarely trigger with lean summaries)
        if len(full_content) > 450000:
            logger.warning("Deliverable content too long (%d chars), truncating to 450k", len(full_content))
            full_content = full_content[:450000] + "\n\n...[Content truncated due to length limits]"

        logger.info("Delivering task %s — payload size: %d chars", task_id, len(full_content))

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
