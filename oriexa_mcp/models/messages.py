"""Message input models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GetMessagesInput(BaseModel):
    task_id: str = Field(..., description="The task ID to get messages for")


class SendMessageInput(BaseModel):
    task_id: str = Field(..., description="The task ID to send a message on")
    content: str = Field(..., description="Message content")
    message_type: str | None = Field(None, description="Message type (e.g. 'text', 'question')")
    structured_data: dict[str, Any] | None = Field(None, description="Structured data for questions/options")
    parent_id: str | None = Field(None, description="Parent message ID for threaded replies")
