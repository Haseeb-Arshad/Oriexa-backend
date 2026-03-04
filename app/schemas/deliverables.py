from pydantic import BaseModel, Field


class CreateDeliverableRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50000)
