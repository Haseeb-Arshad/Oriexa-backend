"""Webhook input models."""

from __future__ import annotations

from pydantic import BaseModel, Field


VALID_EVENTS = [
    "task.new_match",
    "claim.accepted",
    "claim.rejected",
    "deliverable.accepted",
    "deliverable.revision_requested",
]


class CreateWebhookInput(BaseModel):
    url: str = Field(..., description="HTTPS webhook URL to receive events")
    events: list[str] = Field(
        ...,
        min_length=1,
        description=f"Event types to subscribe to: {', '.join(VALID_EVENTS)}",
    )


class DeleteWebhookInput(BaseModel):
    webhook_id: str = Field(..., description="The webhook ID to delete")
