"""Task-related input models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BrowseTasksInput(BaseModel):
    status: str | None = Field(None, description="Filter by status: open, claimed, in_progress, delivered, completed, cancelled, disputed")
    category: str | None = Field(None, description="Filter by category slug")
    min_budget: int | None = Field(None, ge=0, description="Minimum budget in credits")
    max_budget: int | None = Field(None, ge=0, description="Maximum budget in credits")
    sort: str | None = Field(None, description="Sort order: newest, oldest, budget_high, budget_low")
    cursor: str | None = Field(None, description="Pagination cursor from previous response")
    limit: int | None = Field(None, ge=1, le=100, description="Number of results (1-100, default 20)")


class GetTaskInput(BaseModel):
    task_id: str = Field(..., description="The task ID to retrieve")


class CreateTaskInput(BaseModel):
    title: str = Field(..., description="Task title")
    description: str = Field(..., description="Detailed task description")
    requirements: str | None = Field(None, description="Specific requirements or acceptance criteria")
    budget_credits: int = Field(..., gt=0, description="Budget in credits")
    category_id: str | None = Field(None, description="Category ID for the task")
    deadline: str | None = Field(None, description="Deadline in ISO 8601 format")
    max_revisions: int | None = Field(None, ge=0, le=5, description="Maximum revision rounds (0-5)")
