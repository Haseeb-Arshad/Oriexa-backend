"""Claim-related input models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SingleClaimItem(BaseModel):
    task_id: str = Field(..., description="Task ID to claim")
    proposed_credits: int = Field(..., gt=0, description="Proposed amount in credits")
    message: str | None = Field(None, description="Optional cover message")


class ClaimTaskInput(BaseModel):
    task_id: str = Field(..., description="The task ID to claim")
    proposed_credits: int = Field(..., gt=0, description="Proposed amount in credits")
    message: str | None = Field(None, description="Optional cover message for the poster")


class BulkClaimInput(BaseModel):
    claims: list[SingleClaimItem] = Field(
        ..., min_length=1, max_length=10,
        description="List of tasks to claim (max 10)"
    )


class WithdrawClaimInput(BaseModel):
    task_id: str = Field(..., description="The task ID")
    claim_id: str = Field(..., description="The claim ID to withdraw")


class AcceptClaimInput(BaseModel):
    task_id: str = Field(..., description="The task ID")
    claim_id: str = Field(..., description="The claim ID to accept")
