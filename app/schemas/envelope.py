from pydantic import BaseModel
from typing import Any


class Meta(BaseModel):
    timestamp: str
    request_id: str
    cursor: str | None = None
    has_more: bool | None = None
    count: int | None = None


class SuccessResponse(BaseModel):
    ok: bool = True
    data: Any
    meta: Meta


class ErrorDetail(BaseModel):
    code: str
    message: str
    suggestion: str


class ErrorResponse(BaseModel):
    ok: bool = False
    error: ErrorDetail
    meta: Meta
