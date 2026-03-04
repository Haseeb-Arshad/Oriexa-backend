"""Review-related input models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewDeliverableInput(BaseModel):
    task_id: str = Field(..., description="The task ID")
    deliverable_id: str = Field(..., description="The deliverable ID to review")
    verdict: str = Field(..., description="Review verdict: 'pass' or 'fail'")
    feedback: str | None = Field(None, description="Reviewer feedback text")
