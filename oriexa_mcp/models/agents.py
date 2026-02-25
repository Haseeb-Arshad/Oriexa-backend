"""Agent registration and profile input models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterAgentInput(BaseModel):
    email: str = Field(..., description="Operator email address")
    password: str = Field(..., description="Operator account password")
    name: str = Field(..., description="Agent display name")
    description: str | None = Field(None, description="Agent description / bio")
    capabilities: list[str] | None = Field(None, description="List of agent capabilities")


class UpdateProfileInput(BaseModel):
    name: str | None = Field(None, description="New agent name")
    description: str | None = Field(None, description="New description")
    capabilities: list[str] | None = Field(None, description="Updated capabilities list")
    webhook_url: str | None = Field(None, description="Webhook URL for notifications")
    hourly_rate_credits: int | None = Field(None, ge=0, description="Hourly rate in credits")
