"""Communication tools — send clarifications and check for responses via Oriexa API."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import tool

from app.oriexa_client.client import OriexaClient

logger = logging.getLogger(__name__)

# Prefix used to identify clarification messages among deliverables
CLARIFICATION_PREFIX = "[CLARIFICATION]"
RESPONSE_PREFIX = "[RESPONSE]"

_client: OriexaClient | None = None


def _get_client() -> OriexaClient:
    """Lazy-initialise a shared OriexaClient singleton."""
    global _client
    if _client is None:
        _client = OriexaClient()
    return _client


@tool
async def send_clarification(
    task_id: Annotated[int, "The Oriexa task ID to send the clarification for"],
    question: Annotated[str, "The clarification question to ask the task poster"],
) -> str:
    """Send a clarification question to the task poster via a deliverable.

    The question is submitted as a deliverable with a [CLARIFICATION] prefix
    so the task poster knows this is a question, not a final submission.
    Use this when the task requirements are ambiguous or missing details.

    Returns a confirmation message or an error description.
    """
    if not question.strip():
        return "[ERROR] Clarification question cannot be empty."

    client = _get_client()

    # Format the deliverable content with the clarification prefix
    content = f"{CLARIFICATION_PREFIX} {question.strip()}"

    logger.info(
        "send_clarification: task_id=%d question_length=%d",
        task_id, len(question),
    )

    result = await client.submit_deliverable(task_id=task_id, content=content)

    if result is None:
        return (
            f"[ERROR] Failed to send clarification for task {task_id}. "
            "The API request failed — check connectivity and task ownership."
        )

    deliverable_id = result.get("id", "unknown")
    return (
        f"[OK] Clarification sent for task {task_id} "
        f"(deliverable #{deliverable_id}).\n"
        f"Question: {question.strip()}\n"
        f"Use check_response to poll for an answer."
    )


@tool
async def check_response(
    task_id: Annotated[int, "The Oriexa task ID to check for responses"],
    last_seen_id: Annotated[int | None, "ID of the last deliverable already seen (to skip old ones)"] = None,
) -> str:
    """Check if the task poster has responded to a clarification.

    Fetches all deliverables for the task and looks for entries marked with
    [RESPONSE] prefix, or any deliverable posted after our last clarification.
    Returns any new responses found, or a message indicating no response yet.
    """
    client = _get_client()

    logger.info(
        "check_response: task_id=%d last_seen_id=%s",
        task_id, last_seen_id,
    )

    deliverables = await client.get_deliverables(task_id=task_id)

    if not deliverables:
        return f"[INFO] No deliverables found for task {task_id}."

    # Filter to only new deliverables (after last_seen_id if provided)
    new_deliverables: list[dict[str, Any]] = []
    for d in deliverables:
        d_id = d.get("id")
        if last_seen_id is not None and isinstance(d_id, int) and d_id <= last_seen_id:
            continue
        new_deliverables.append(d)

    if not new_deliverables:
        return (
            f"[INFO] No new deliverables for task {task_id} since "
            f"deliverable #{last_seen_id}. The poster has not responded yet."
        )

    # Look for explicit responses, or any non-clarification messages
    responses: list[str] = []
    other_messages: list[str] = []

    for d in new_deliverables:
        d_id = d.get("id", "?")
        content = d.get("content", "")
        created = d.get("createdAt", d.get("created_at", ""))

        if content.startswith(RESPONSE_PREFIX):
            body = content[len(RESPONSE_PREFIX):].strip()
            responses.append(f"  [#{d_id}] {created}: {body}")
        elif not content.startswith(CLARIFICATION_PREFIX):
            # Any non-clarification message might be a response
            other_messages.append(f"  [#{d_id}] {created}: {content}")

    parts: list[str] = []

    if responses:
        parts.append(f"Found {len(responses)} explicit response(s):")
        parts.extend(responses)

    if other_messages:
        parts.append(f"Found {len(other_messages)} other message(s) (may be responses):")
        parts.extend(other_messages)

    if not responses and not other_messages:
        # All new deliverables were our own clarifications
        return (
            f"[INFO] {len(new_deliverables)} new deliverable(s) found for task "
            f"{task_id}, but all are outgoing clarifications. No response yet."
        )

    # Track the latest ID for the next poll
    latest_id = max(d.get("id", 0) for d in new_deliverables if isinstance(d.get("id"), int))
    parts.append(f"\nLatest deliverable ID: {latest_id} (pass as last_seen_id on next check)")

    return "\n".join(parts)
