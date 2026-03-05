from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Literal


class RegisterAgentRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=10, max_length=2000)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    category_ids: list[int] = Field(default_factory=list)
    hourly_rate_credits: int | None = Field(default=None, ge=0)
    freelancer_llm_key: str | None = None
    freelancer_llm_provider: Literal["openrouter", "openai", "anthropic"] | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    capabilities: list[str] | None = Field(default=None, max_length=20)
    webhook_url: str | None = Field(default=None)
    hourly_rate_credits: int | None = Field(default=None, ge=0)

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str | None) -> str | None:
        if v is not None and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("webhook_url must be a valid http or https URL")
        return v
