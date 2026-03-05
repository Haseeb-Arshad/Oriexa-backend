from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import Optional

class UpdateAgentRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=2000)
    capabilities: Optional[list[str]] = Field(default=None, max_length=20)
    webhook_url: Optional[str] = None
    hourly_rate_credits: Optional[int] = Field(default=None, ge=0)

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: str | None) -> str | None:
        if v is not None and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("webhook_url must be a valid http or https URL")
        return v

try:
    data = UpdateAgentRequest(webhook_url="not-a-url")
    print("Success:", data)
except ValidationError as e:
    print("Caught expected validation error:")
    print(e)
except Exception as e:
    print("Caught unexpected exception:")
    print(e)
