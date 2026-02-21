from pydantic import BaseModel, Field
from typing import Literal


class ReviewRequest(BaseModel):
    deliverable_id: int
    verdict: Literal["pass", "fail"]
    feedback: str | None = None
    scores: dict | None = None
    model_used: str | None = None
    key_source: Literal["poster", "freelancer", "none"] = "none"
