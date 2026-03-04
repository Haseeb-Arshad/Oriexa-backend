"""Orchestrator input models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrchestratorStartInput(BaseModel):
    task_id: str = Field(..., description="The Oriexa task ID to start orchestration for")


class OrchestratorStatusInput(BaseModel):
    execution_id: str = Field(..., description="The orchestrator execution ID")


class OrchestratorListInput(BaseModel):
    status: str | None = Field(None, description="Filter by status")
    limit: int | None = Field(None, ge=1, le=100, description="Number of results (default 20)")
    offset: int | None = Field(None, ge=0, description="Result offset (default 0)")
