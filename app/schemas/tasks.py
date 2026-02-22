from pydantic import BaseModel, Field
from typing import Literal


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=20, max_length=5000)
    requirements: str | None = Field(default=None, max_length=5000)
    budget_credits: int = Field(ge=10)
    category_id: int | None = Field(default=None, gt=0)
    deadline: str | None = None
    max_revisions: int | None = Field(default=None, ge=0, le=5)
    auto_review_enabled: bool = False
    poster_llm_key: str | None = None
    poster_llm_provider: Literal["openrouter", "openai", "anthropic"] | None = None
    poster_max_reviews: int | None = Field(default=None, gt=0)


class BrowseTasksParams(BaseModel):
    status: Literal["open", "claimed", "in_progress", "delivered", "completed"] = "open"
    category: int | None = Field(default=None, gt=0)
    min_budget: int | None = Field(default=None, ge=0)
    max_budget: int | None = Field(default=None, ge=0)
    sort: Literal["newest", "oldest", "budget_high", "budget_low"] = "newest"
    cursor: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class UpdateTaskRequest(BaseModel):
    description: str | None = Field(default=None, min_length=20, max_length=5000)
    requirements: str | None = Field(default=None, max_length=5000)
