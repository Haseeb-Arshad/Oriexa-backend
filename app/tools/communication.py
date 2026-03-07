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

    # Deduplicate questions: if an equivalent question already exists for this task,
    # reuse it instead of posting another copy.
    existing = await client._request("GET", f"/tasks/{task_id}/messages")
    if isinstance(existing, list):
        normalized_content = _normalize_question_text(content)
        normalized_options = _normalize_options(options)
        for msg in reversed(existing):
            if msg.get("message_type") != "question":
                continue
            if msg.get("sender_type") != "agent":
                continue
            if _normalize_question_text(str(msg.get("content", ""))) != normalized_content:
                continue

            existing_structured = msg.get("structured_data") or {}
            if str(existing_structured.get("question_type", "")) != question_type:
                continue
            if question_type == "multiple_choice":
                existing_opts = _normalize_options(existing_structured.get("options"))
                if existing_opts != normalized_options:
                    continue

            already_answered = bool(existing_structured.get("response")) or bool(existing_structured.get("responded_at"))
            return {
                "ok": True,
                "message_id": msg.get("id"),
                "deduped": True,
                "already_answered": already_answered,
            }

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
    return {
        "ok": True,
        "message_id": message_id,
        "deduped": False,
        "already_answered": False,
    }


@tool
async def send_chat_message(
    oriexa_task_id: Annotated[int, "The Oriexa task ID (injected automatically)"],
    content: Annotated[str, "The text message to send to the user"],
) -> dict[str, Any]:
    """Send a plain text chat message to the user.
    Use this to reply to user inquiries, provide progress updates, or ask simple questions.
    Do NOT use this if you need a structured response (use post_question instead).
    """
    if not content.strip():
        return {"ok": False, "error": "Message content cannot be empty."}

    client = _get_client()

    logger.info("send_chat_message: task_id=%d content_length=%d", oriexa_task_id, len(content))

    result = await client._request(
        "POST",
        f"/tasks/{oriexa_task_id}/messages",
        json={
            "content": content.strip(),
            "message_type": "text",
        },
    )

    if result is None:
        return {"ok": False, "error": f"Failed to send message for task {oriexa_task_id}."}

    return {"ok": True, "message_id": result.get("id")}


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


def _normalize_question_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _normalize_options(options: Any) -> list[str]:
    if not isinstance(options, list):
        return []
    return [" ".join(str(opt).strip().lower().split()) for opt in options]
