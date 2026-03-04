"""Communication tools — post questions and read messages via Oriexa messages API."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import tool

from app.oriexa_client.client import OriexaClient

logger = logging.getLogger(__name__)

_client: OriexaClient | None = None


def _get_client() -> OriexaClient:
    """Lazy-initialise a shared OriexaClient singleton."""
    global _client
    if _client is None:
        _client = OriexaClient()
    return _client


@tool
async def post_question(
    task_id: Annotated[int, "The Oriexa task ID"],
    content: Annotated[str, "The question text to display to the task poster"],
    question_type: Annotated[
        str,
        "Type of question: 'yes_no', 'multiple_choice', or 'text_input'",
    ],
    options: Annotated[
        list[str] | None,
        "List of choices for multiple_choice questions (2-4 options). Not needed for yes_no or text_input.",
    ] = None,
    prompt: Annotated[
        str | None,
        "Optional placeholder/hint for text_input questions",
    ] = None,
) -> dict[str, Any]:
    """Post a structured question to the task poster via the messages API.

    The question renders as an interactive card in the poster's UI:
    - yes_no: Two buttons (Yes / No)
    - multiple_choice: Radio buttons with 2-4 options
    - text_input: Free-form text field with optional placeholder

    Returns {ok: True, message_id: int} on success, or {ok: False, error: str}.
    """
    if not content.strip():
        return {"ok": False, "error": "Question content cannot be empty."}

    if question_type not in ("yes_no", "multiple_choice", "text_input"):
        return {"ok": False, "error": f"Invalid question_type: {question_type}. Use yes_no, multiple_choice, or text_input."}

    if question_type == "multiple_choice":
        if not options or len(options) < 2:
            return {"ok": False, "error": "multiple_choice requires at least 2 options."}
        if len(options) > 4:
            options = options[:4]

    structured_data: dict[str, Any] = {"question_type": question_type}
    if options:
        structured_data["options"] = options
    if prompt:
        structured_data["prompt"] = prompt

    client = _get_client()

    logger.info(
        "post_question: task_id=%d type=%s content_length=%d",
        task_id, question_type, len(content),
    )

    result = await client._request(
        "POST",
        f"/tasks/{task_id}/messages",
        json={
            "content": content.strip(),
            "message_type": "question",
            "structured_data": structured_data,
        },
    )

    if result is None:
        return {"ok": False, "error": f"Failed to post question for task {task_id}. API request failed."}

    message_id = result.get("id")
    return {"ok": True, "message_id": message_id}


@tool
async def read_task_messages(
    task_id: Annotated[int, "The Oriexa task ID to read messages for"],
    after_message_id: Annotated[
        int | None,
        "Only return messages with ID greater than this value (for polling new messages)",
    ] = None,
) -> dict[str, Any]:
    """Read all messages in a task conversation.

    Returns the full message history ordered by created_at.
    Use after_message_id to only get messages newer than a known ID.

    Returns {ok: True, messages: [...]} or {ok: False, error: str}.
    """
    client = _get_client()

    logger.info(
        "read_task_messages: task_id=%d after_message_id=%s",
        task_id, after_message_id,
    )

    result = await client._request("GET", f"/tasks/{task_id}/messages")

    if result is None:
        return {"ok": False, "error": f"Failed to fetch messages for task {task_id}."}

    messages: list[dict[str, Any]] = result if isinstance(result, list) else []

    # Filter to messages after the given ID if specified
    if after_message_id is not None:
        messages = [
            m for m in messages
            if isinstance(m.get("id"), int) and m["id"] > after_message_id
        ]

    return {"ok": True, "messages": messages}
