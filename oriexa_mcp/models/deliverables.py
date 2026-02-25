"""Deliverable-related input models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubmitDeliverableInput(BaseModel):
    task_id: str = Field(..., description="The task ID to submit a deliverable for")
    content: str = Field(..., description="The deliverable content (code, text, files, etc.)")


class AcceptDeliverableInput(BaseModel):
    task_id: str = Field(..., description="The task ID")
    deliverable_id: str = Field(..., description="The deliverable ID to accept")


class RejectDeliverableInput(BaseModel):
    task_id: str = Field(..., description="The task ID")
    deliverable_id: str = Field(..., description="The deliverable ID to reject/request revision")
    revision_notes: str | None = Field(None, description="Notes explaining what needs revision")
