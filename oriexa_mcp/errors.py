"""Custom exceptions and user-friendly error formatting."""

from __future__ import annotations


class OriexaAPIError(Exception):
    """Raised when the Oriexa REST API returns a non-2xx response."""

    def __init__(self, status_code: int, code: str, message: str, suggestion: str = "") -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(self.friendly())

    def friendly(self) -> str:
        parts = [f"**Error {self.status_code}** — {self.code}: {self.message}"]
        if self.suggestion:
            parts.append(f"**Suggestion:** {self.suggestion}")
        return "\n".join(parts)


class AuthError(OriexaAPIError):
    """Authentication / authorization failure (401/403)."""

    def __init__(self, message: str = "Invalid or expired API key") -> None:
        super().__init__(401, "AUTH_ERROR", message, "Check your ORIEXA_API_KEY.")


class NotFoundError(OriexaAPIError):
    """Resource not found (404)."""

    def __init__(self, resource: str = "Resource") -> None:
        super().__init__(404, "NOT_FOUND", f"{resource} not found.", "Verify the ID and try again.")


class RateLimitError(OriexaAPIError):
    """Rate limit exceeded (429)."""

    def __init__(self) -> None:
        super().__init__(
            429,
            "RATE_LIMITED",
            "Rate limit exceeded (100 req/min).",
            "Wait a moment before retrying.",
        )


def parse_api_error(status_code: int, body: dict) -> OriexaAPIError:
    """Parse an API error envelope into the appropriate exception."""
    error = body.get("error", {})
    detail = body.get("detail")

    if isinstance(error, str):
        code = "REQUEST_FAILED"
        message = error
        suggestion = ""
    else:
        code = error.get("code", "UNKNOWN")
        message = error.get("message", "Unknown error")
        suggestion = error.get("suggestion", "")

    if detail and message == "Unknown error":
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict):
                message = first.get("msg") or first.get("message") or str(first)
            else:
                message = str(first)
        else:
            message = str(detail)

    if status_code in (401, 403):
        return AuthError(message)
    if status_code == 404:
        return NotFoundError(message)
    if status_code == 429:
        return RateLimitError()
    return OriexaAPIError(status_code, code, message, suggestion)
