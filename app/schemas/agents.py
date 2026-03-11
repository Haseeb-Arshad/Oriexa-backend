from pydantic import BaseModel, Field, field_validator


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
