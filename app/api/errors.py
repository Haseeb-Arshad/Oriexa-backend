"""Port of Oriexa/src/lib/api/errors.ts — every error code, message, and suggestion is identical."""

from fastapi.responses import JSONResponse

from app.api.envelope import error_response


# 401
def unauthorized_error(detail: str | None = None) -> JSONResponse:
    return error_response(
        401,
        "UNAUTHORIZED",
        detail or "Missing or invalid Authorization header",
        "Include header: Authorization: Bearer th_agent_<your-key>",
    )


def invalid_api_key_error() -> JSONResponse:
    return error_response(
        401,
        "UNAUTHORIZED",
        "Invalid API key",
        "Check your API key or generate a new one at /dashboard/agents",
    )


# 403
def forbidden_error(reason: str, suggestion: str) -> JSONResponse:
    return error_response(403, "FORBIDDEN", reason, suggestion)


def agent_suspended_error() -> JSONResponse:
    return error_response(
        403,
        "FORBIDDEN",
        "Agent is suspended",
        "Contact your account administrator",
    )


def agent_paused_error() -> JSONResponse:
    return error_response(
        403,
        "FORBIDDEN",
        "Agent is paused",
        "Reactivate your agent at /dashboard/agents",
    )


# 404
def not_found_error(entity: str, id: int, suggestion: str) -> JSONResponse:
    return error_response(
        404,
        f"{entity.upper()}_NOT_FOUND",
        f"{entity} {id} does not exist",
        suggestion,
    )


def task_not_found_error(id: int) -> JSONResponse:
    return not_found_error(
        "Task",
        id,
        "Use GET /api/v1/tasks to browse available tasks",
    )


# 409
def conflict_error(code: str, message: str, suggestion: str) -> JSONResponse:
    return error_response(409, code, message, suggestion)


def task_not_open_error(task_id: int, current_status: str) -> JSONResponse:
    return conflict_error(
        "TASK_NOT_OPEN",
        f"Task {task_id} is not open (current status: {current_status})",
        "This task has already been claimed. Browse open tasks with GET /api/v1/tasks?status=open",
    )


def duplicate_claim_error(task_id: int) -> JSONResponse:
    return conflict_error(
        "DUPLICATE_CLAIM",
        f"You already have a pending claim on task {task_id}",
        "Check your claims with GET /api/v1/agents/me/claims",
    )


def invalid_status_error(task_id: int, current_status: str, suggestion: str) -> JSONResponse:
    return conflict_error(
        "INVALID_STATUS",
        f"Task {task_id} is not in a deliverable state (status: {current_status})",
        suggestion,
    )


def max_revisions_error(task_id: int, current: int, max_val: int) -> JSONResponse:
    return conflict_error(
        "MAX_REVISIONS",
        f"Maximum revisions reached ({current} of {max_val} deliveries)",
        "No more revisions allowed. Contact the poster.",
    )


# 422
def validation_error(message: str, suggestion: str) -> JSONResponse:
    return error_response(422, "VALIDATION_ERROR", message, suggestion)


# 400
def invalid_parameter_error(message: str, suggestion: str) -> JSONResponse:
    return error_response(400, "INVALID_PARAMETER", message, suggestion)


def invalid_credits_error(proposed: int, budget: int) -> JSONResponse:
    return error_response(
        422,
        "INVALID_CREDITS",
        f"proposed_credits ({proposed}) exceeds task budget ({budget})",
        f"Propose credits \u2264 {budget}",
    )


# 429
def rate_limited_error(retry_after_seconds: int) -> JSONResponse:
    return error_response(
        429,
        "RATE_LIMITED",
        "Rate limit exceeded (100 requests/minute)",
        f"Wait {retry_after_seconds} seconds before retrying. Check X-RateLimit-Reset header.",
    )


# Idempotency errors
def idempotency_key_too_long_error() -> JSONResponse:
    return error_response(
        400,
        "IDEMPOTENCY_KEY_TOO_LONG",
        "Idempotency-Key exceeds maximum length of 255 characters",
        "Use a shorter key, such as a UUID (36 characters)",
    )


def idempotency_key_mismatch_error() -> JSONResponse:
    return error_response(
        422,
        "IDEMPOTENCY_KEY_MISMATCH",
        "Idempotency-Key was already used with a different request path or body",
        "Use a unique Idempotency-Key for each distinct request",
    )


def idempotency_key_in_flight_error() -> JSONResponse:
    return error_response(
        409,
        "IDEMPOTENCY_KEY_IN_FLIGHT",
        "A request with this Idempotency-Key is already being processed",
        "Wait for the original request to complete, then retry",
    )


# Webhook errors
def webhook_not_found_error(id: int) -> JSONResponse:
    return not_found_error(
        "Webhook",
        id,
        "Use GET /api/v1/webhooks to list your webhooks",
    )


def max_webhooks_error() -> JSONResponse:
    return conflict_error(
        "MAX_WEBHOOKS",
        "Maximum of 5 webhooks per agent reached",
        "Delete an existing webhook before adding a new one",
    )


def webhook_forbidden_error() -> JSONResponse:
    return error_response(
        403,
        "FORBIDDEN",
        "This webhook does not belong to your agent",
        "You can only manage your own webhooks",
    )


# Rollback errors
def task_not_claimed_error(task_id: int, current_status: str) -> JSONResponse:
    return conflict_error(
        "TASK_NOT_CLAIMED",
        f"Task {task_id} is not in claimed status (current status: {current_status})",
        "Only claimed tasks can be rolled back to open",
    )


def rollback_forbidden_error() -> JSONResponse:
    return error_response(
        403,
        "FORBIDDEN",
        "Only the task poster can rollback a task",
        "You must be the poster of this task to perform a rollback",
    )


# 500
def internal_error() -> JSONResponse:
    return error_response(
        500,
        "INTERNAL_ERROR",
        "An unexpected error occurred",
        "Try again later. If the issue persists, contact support.",
    )
