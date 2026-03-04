import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse


def _generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def success_response(
    data: Any,
    status_code: int = 200,
    pagination: dict | None = None,
) -> JSONResponse:
    meta: dict[str, Any] = {
        "timestamp": _now_iso(),
        "request_id": _generate_request_id(),
    }
    if pagination:
        meta["cursor"] = pagination.get("cursor")
        meta["has_more"] = pagination.get("has_more")
        meta["count"] = pagination.get("count")

    return JSONResponse(
        content={"ok": True, "data": data, "meta": meta},
        status_code=status_code,
    )


def error_response(
    status_code: int,
    code: str,
    message: str,
    suggestion: str,
) -> JSONResponse:
    return JSONResponse(
        content={
            "ok": False,
            "error": {"code": code, "message": message, "suggestion": suggestion},
            "meta": {
                "timestamp": _now_iso(),
                "request_id": _generate_request_id(),
            },
        },
        status_code=status_code,
    )
