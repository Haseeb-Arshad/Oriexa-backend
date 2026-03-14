from pydantic import BaseModel, Field, field_validator
from typing import Literal


WebhookEventType = Literal[
    "task.new_match",
    "task.updated",
    "claim.created",
    "claim.accepted",
    "claim.rejected",
    "deliverable.submitted",
    "deliverable.accepted",
    "deliverable.revision_requested",
    "message.created",
]


class CreateWebhookRequest(BaseModel):
    url: str = Field(max_length=500)
    events: list[WebhookEventType] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("Webhook URL must use HTTPS")
        return v
