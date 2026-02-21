"""Cursor-based pagination utilities — identical encoding to TypeScript version."""

import base64
import json
from typing import Any


def encode_cursor(id: int, sort_value: str | None = None) -> str:
    payload: dict[str, Any] = {"id": id}
    if sort_value:
        payload["v"] = sort_value
    return base64.b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any] | None:
    try:
        raw = base64.b64decode(cursor).decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed.get("id"), int):
            return None
        return parsed
    except Exception:
        return None
