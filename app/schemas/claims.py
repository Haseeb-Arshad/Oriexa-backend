from pydantic import BaseModel, Field


class CreateClaimRequest(BaseModel):
    proposed_credits: int = Field(ge=1)
    message: str | None = Field(default=None, max_length=1000)


class BulkClaimItem(BaseModel):
    task_id: int
    proposed_credits: int = Field(ge=1)
    message: str | None = Field(default=None, max_length=1000)


class BulkClaimsRequest(BaseModel):
    claims: list[BulkClaimItem] = Field(min_length=1, max_length=10)
