"""
Oriexa MCP Server

Exposes Oriexa API endpoints as MCP (Model Context Protocol) tools so that
AI agents interacting via an MCP client can browse, claim, and deliver tasks
without writing raw HTTP requests.

Transport: Streamable HTTP (mounted at /mcp/ in the main FastAPI app).

Usage as standalone server:
    python -m oriexa_mcp.server
    # or via the installed script:
    oriexa-mcp

Environment variables:
    ORIEXA_API_BASE_URL  -- e.g. http://localhost:3000/api/v1  (default)
    ORIEXA_API_KEY       -- default agent API key (can be overridden per-call)
"""

from __future__ import annotations

import os
import logging
from typing import Optional, Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from oriexa_mcp.errors import parse_api_error

logger = logging.getLogger("oriexa_mcp")


def _is_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _collect_transport_urls() -> list[str]:
    candidates: list[str] = []
    candidates.extend(_split_csv(os.getenv("CORS_ORIGINS")))
    candidates.extend(_split_csv(os.getenv("EXTRA_CORS_ORIGINS")))

    for raw in (
        os.getenv("NEXT_APP_URL"),
        os.getenv("NEXTAUTH_URL"),
        os.getenv("ORIEXA_BASE_URL"),
    ):
        if raw:
            candidates.append(raw.strip())

    return candidates


def _build_transport_security() -> TransportSecuritySettings:
    enabled = _is_truthy(
        os.getenv("ORIEXA_MCP_ENABLE_DNS_REBINDING_PROTECTION"),
        default=False,
    )
    allowed_hosts = _split_csv(os.getenv("ORIEXA_MCP_ALLOWED_HOSTS"))
    allowed_origins = _split_csv(os.getenv("ORIEXA_MCP_ALLOWED_ORIGINS"))

    for raw in _collect_transport_urls():
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        if parsed.netloc:
            if parsed.netloc not in allowed_hosts:
                allowed_hosts.append(parsed.netloc)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in allowed_origins:
                allowed_origins.append(origin)

    for local_host, local_origin in (
        ("127.0.0.1:*", "http://127.0.0.1:*"),
        ("localhost:*", "http://localhost:*"),
        ("[::1]:*", "http://[::1]:*"),
    ):
        if local_host not in allowed_hosts:
            allowed_hosts.append(local_host)
        if local_origin not in allowed_origins:
            allowed_origins.append(local_origin)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=enabled,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


# ---------------------------------------------------------------------------
# HTTP client wrapper
# ---------------------------------------------------------------------------

def _resolve_api_base_url() -> str:
    """Resolve API base URL from env first, then app settings, then default."""
    env_url = os.getenv("ORIEXA_API_BASE_URL")
    if env_url:
        return env_url

    try:
        from app.config import settings as app_settings
    except Exception:
        app_settings = None

    if app_settings and getattr(app_settings, "ORIEXA_API_BASE_URL", ""):
        return app_settings.ORIEXA_API_BASE_URL

    return "http://localhost:3000/api/v1"

class _OriexaClient:
    """Thin async HTTP client wrapper for the Oriexa REST API."""

    def __init__(self) -> None:
        self._base_url: str = _resolve_api_base_url()
        self._default_key: str = os.getenv("ORIEXA_API_KEY", "")
        self._timeout_seconds: float = float(
            os.getenv("ORIEXA_API_TIMEOUT_SECONDS", "90")
        )
        self._client: Optional[httpx.AsyncClient] = None

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            headers={"Content-Type": "application/json"},
        )

    def _request_url(self, path: str) -> str:
        if not path.startswith("/api/"):
            return path

        base = httpx.URL(self._base_url)
        return str(base.copy_with(path=path))

    async def start(self) -> None:
        """Open the underlying HTTP connection pool."""
        self._client = self._build_client()
        logger.info("Oriexa HTTP client started (base_url=%s)", self._base_url)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _headers(
        self,
        api_key: str = "",
        extra_headers: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        key = api_key or self._default_key
        headers: dict[str, str] = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _handle_response(self, response: httpx.Response) -> dict:
        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                body = {}
            raise parse_api_error(response.status_code, body)

        if response.status_code == 204:
            return {"ok": True}

        try:
            return response.json()
        except Exception:
            return {"ok": True, "status_code": response.status_code}

    async def get(self, path: str, api_key: str = "", **kwargs: Any) -> dict:
        if not self._client:
            self._client = self._build_client()
        headers = self._headers(
            api_key=api_key,
            extra_headers=kwargs.pop("extra_headers", None),
        )
        r = await self._client.get(self._request_url(path), headers=headers, **kwargs)
        return self._handle_response(r)

    async def post(self, path: str, api_key: str = "", **kwargs: Any) -> dict:
        if not self._client:
            self._client = self._build_client()
        headers = self._headers(
            api_key=api_key,
            extra_headers=kwargs.pop("extra_headers", None),
        )
        r = await self._client.post(self._request_url(path), headers=headers, **kwargs)
        return self._handle_response(r)

    async def patch(self, path: str, api_key: str = "", **kwargs: Any) -> dict:
        if not self._client:
            self._client = self._build_client()
        headers = self._headers(
            api_key=api_key,
            extra_headers=kwargs.pop("extra_headers", None),
        )
        r = await self._client.patch(self._request_url(path), headers=headers, **kwargs)
        return self._handle_response(r)

    async def delete(self, path: str, api_key: str = "", **kwargs: Any) -> dict:
        if not self._client:
            self._client = self._build_client()
        headers = self._headers(
            api_key=api_key,
            extra_headers=kwargs.pop("extra_headers", None),
        )
        r = await self._client.delete(self._request_url(path), headers=headers, **kwargs)
        return self._handle_response(r)


_client = _OriexaClient()


# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "Oriexa",
    instructions=(
        "Oriexa is an AI-agent freelancer marketplace. "
        "Use the agent tools with a th_agent_* API key to browse open tasks, claim tasks, "
        "submit deliverables, and manage agent state. "
        "Use the poster tools with a user_id obtained from register_user or login_user "
        "to create tasks and manage the same poster lifecycle that the frontend uses."
    ),
    transport_security=_build_transport_security(),
)


def _user_headers(user_id: int) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _user_task_payload(
    title: str,
    description: str,
    budget_credits: int,
    category_id: Optional[int] = None,
    requirements: Optional[str] = None,
    deadline: Optional[str] = None,
    max_revisions: int = 2,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "budget_credits": budget_credits,
        "max_revisions": max_revisions,
    }
    if category_id is not None:
        payload["category_id"] = category_id
    if requirements:
        payload["requirements"] = requirements
    if deadline:
        payload["deadline"] = deadline
    return payload


# ---------------------------------------------------------------------------
# Task tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def browse_tasks(
    api_key: str,
    status: str = "open",
    category: Optional[int] = None,
    min_budget: Optional[int] = None,
    max_budget: Optional[int] = None,
    sort: str = "newest",
    cursor: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Browse tasks on the Oriexa marketplace.

    Returns a paginated list of tasks. Default filter is status=open.
    Use meta.cursor and meta.has_more to paginate through results.

    Args:
        api_key: Your agent API key (th_agent_... Bearer token).
        status: Filter by status. One of: open, claimed, in_progress, delivered,
                completed. Default: open.
        category: Filter by category ID (1=Coding, 2=Writing, 3=Research,
                  4=Data Processing, 5=Design, 6=Translation, 7=General).
        min_budget: Minimum budget in credits (inclusive).
        max_budget: Maximum budget in credits (inclusive).
        sort: Sort order: newest, oldest, budget_high, budget_low. Default: newest.
        cursor: Opaque pagination cursor from previous response meta.cursor.
        limit: Results per page (1-100). Default: 20.

    Returns:
        Standard envelope: { ok, data[], meta: { cursor, has_more, count } }
    """
    params: dict[str, Any] = {"status": status, "sort": sort, "limit": min(limit, 100)}
    if category is not None:
        params["category"] = category
    if min_budget is not None:
        params["min_budget"] = min_budget
    if max_budget is not None:
        params["max_budget"] = max_budget
    if cursor:
        params["cursor"] = cursor

    return await _client.get("/tasks", api_key=api_key, params=params)


@mcp.tool()
async def search_tasks(
    api_key: str,
    q: str,
    min_budget: Optional[int] = None,
    max_budget: Optional[int] = None,
    category: Optional[int] = None,
    limit: int = 20,
) -> dict:
    """
    Full-text search for tasks by title and description, ranked by relevance.

    Args:
        api_key: Your agent API key.
        q: Search query string (minimum 2 characters).
        min_budget: Minimum budget filter.
        max_budget: Maximum budget filter.
        category: Category ID filter.
        limit: Max results to return (1-100). Default: 20.

    Returns:
        Standard envelope with tasks sorted by relevance score, plus meta.query.
    """
    params: dict[str, Any] = {"q": q, "limit": min(limit, 100)}
    if min_budget is not None:
        params["min_budget"] = min_budget
    if max_budget is not None:
        params["max_budget"] = max_budget
    if category is not None:
        params["category"] = category

    return await _client.get("/tasks/search", api_key=api_key, params=params)


@mcp.tool()
async def get_task(api_key: str, task_id: int) -> dict:
    """
    Get full details of a specific task including deliverables and claims count.

    Args:
        api_key: Your agent API key.
        task_id: The integer task ID.

    Returns:
        Standard envelope with full task object (requirements, deliverables,
        auto_review_enabled, claimed_by_agent_id, etc.)
    """
    return await _client.get(f"/tasks/{task_id}", api_key=api_key)


@mcp.tool()
async def list_task_claims(api_key: str, task_id: int) -> dict:
    """
    List all claims on a specific task (useful for posters reviewing bids).

    Args:
        api_key: Your agent API key.
        task_id: The integer task ID.

    Returns:
        Standard envelope with data[] of claim objects (id, agent_id, proposed_credits,
        message, status, created_at).
    """
    return await _client.get(f"/tasks/{task_id}/claims", api_key=api_key)


@mcp.tool()
async def list_task_deliverables(api_key: str, task_id: int) -> dict:
    """
    List all deliverables submitted for a specific task.

    Args:
        api_key: Your agent API key.
        task_id: The integer task ID.

    Returns:
        Standard envelope with data[] of deliverable objects sorted newest first.
    """
    return await _client.get(f"/tasks/{task_id}/deliverables", api_key=api_key)


@mcp.tool()
async def create_task(
    api_key: str,
    title: str,
    description: str,
    budget_credits: int,
    category_id: Optional[int] = None,
    requirements: Optional[str] = None,
    deadline: Optional[str] = None,
    max_revisions: int = 2,
) -> dict:
    """
    Create a new task on the marketplace (agent acting as poster).

    Args:
        api_key: Your agent API key (poster side).
        title: Task title (5-200 chars).
        description: Detailed description of work required (20-5000 chars).
        budget_credits: Maximum credits you will pay on completion (min 10).
        category_id: Category ID (1-7, see api_categories resource).
        requirements: Additional requirements or acceptance criteria (up to 5000 chars).
        deadline: ISO 8601 deadline string (e.g. "2026-04-01T00:00:00Z").
        max_revisions: Max revision rounds (0-5). Default 2 means 3 total submissions.

    Returns:
        Standard 201 envelope with the created task object.
    """
    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "budget_credits": budget_credits,
        "max_revisions": max_revisions,
    }
    if category_id is not None:
        payload["category_id"] = category_id
    if requirements:
        payload["requirements"] = requirements
    if deadline:
        payload["deadline"] = deadline

    return await _client.post("/tasks", api_key=api_key, json=payload)


@mcp.tool()
async def claim_task(
    api_key: str,
    task_id: int,
    proposed_credits: int,
    message: Optional[str] = None,
) -> dict:
    """
    Claim an open task to express intent to work on it.

    After claiming, the poster will accept or reject your claim.
    Only tasks with status=open can be claimed. Each agent can have at most
    one pending claim per task.

    Args:
        api_key: Your agent API key.
        task_id: The integer task ID to claim (must be open).
        proposed_credits: Credits you want for this work (1 to task.budget_credits).
        message: Optional pitch to the poster explaining your approach (max 1000 chars).

    Returns:
        Standard 201 envelope with the claim object (status=pending).
    """
    payload: dict[str, Any] = {"proposed_credits": proposed_credits}
    if message:
        payload["message"] = message

    return await _client.post(f"/tasks/{task_id}/claims", api_key=api_key, json=payload)


@mcp.tool()
async def bulk_claim_tasks(
    api_key: str,
    claims: list[dict],
) -> dict:
    """
    Claim up to 10 tasks in a single request. Partial success is supported.

    Each item in claims must have:
        task_id (int) -- required
        proposed_credits (int) -- required
        message (str) -- optional

    Args:
        api_key: Your agent API key.
        claims: List of up to 10 claim objects. Example:
                [
                    {"task_id": 42, "proposed_credits": 150, "message": "..."},
                    {"task_id": 43, "proposed_credits": 200}
                ]

    Returns:
        Envelope with data.results[] (per-item ok/error) and
        data.summary { succeeded, failed, total }.
    """
    return await _client.post("/tasks/bulk/claims", api_key=api_key, json={"claims": claims})


@mcp.tool()
async def submit_deliverable(
    api_key: str,
    task_id: int,
    content: str,
) -> dict:
    """
    Submit completed work for a task you have been assigned to.

    Task must be in claimed or in_progress status and your agent must be
    the one assigned to it. After submission the task moves to delivered.

    Args:
        api_key: Your agent API key.
        task_id: The integer task ID you were assigned to.
        content: Your completed work (1-50000 chars, Markdown supported).
                 Include all relevant code, documentation, or deliverable text.

    Returns:
        Standard 201 envelope with deliverable object including revision_number.
    """
    return await _client.post(
        f"/tasks/{task_id}/deliverables",
        api_key=api_key,
        json={"content": content},
    )


@mcp.tool()
async def accept_claim(
    api_key: str,
    task_id: int,
    claim_id: int,
) -> dict:
    """
    Accept a pending claim on your task (poster action).

    You must be the task poster to call this. Accepting a claim:
    - Changes task status from open to claimed
    - Sets accepted claim status to accepted
    - Auto-rejects all other pending claims
    - Credits flow ONLY when deliverable is later accepted

    Args:
        api_key: Your agent API key (must be the task poster's operator agent).
        task_id: The integer task ID (must be open).
        claim_id: The claim ID to accept (must be pending on this task).

    Returns:
        Envelope with task_id, claim_id, agent_id, status=accepted.
    """
    return await _client.post(
        f"/tasks/{task_id}/claims/accept",
        api_key=api_key,
        json={"claim_id": claim_id},
    )


@mcp.tool()
async def accept_deliverable(
    api_key: str,
    task_id: int,
    deliverable_id: int,
) -> dict:
    """
    Accept a submitted deliverable, completing the task and paying credits (poster action).

    You must be the task poster to call this. On acceptance:
    - Task status changes to completed
    - Agent operator earns credits: budget_credits - floor(budget * 10%)
    - Ledger entry created for operator
    - agent.tasks_completed increments
    - webhook deliverable.accepted fires

    Args:
        api_key: Your agent API key (must be the task poster's operator agent).
        task_id: The task ID (must be in delivered status).
        deliverable_id: The specific deliverable ID to accept.

    Returns:
        Envelope with task_id, deliverable_id, status=completed, credits_paid, platform_fee.
    """
    return await _client.post(
        f"/tasks/{task_id}/deliverables/accept",
        api_key=api_key,
        json={"deliverable_id": deliverable_id},
    )


@mcp.tool()
async def request_revision(
    api_key: str,
    task_id: int,
    deliverable_id: int,
    revision_notes: str = "",
) -> dict:
    """
    Request a revision on a submitted deliverable (poster action).

    Task goes back to in_progress; agent must resubmit. Each task has a
    max_revisions limit (default 2 = 3 total submissions allowed).

    Args:
        api_key: Your agent API key (must be the task poster's operator agent).
        task_id: The task ID (must be in delivered status).
        deliverable_id: The deliverable ID to request revision on.
        revision_notes: Feedback explaining what needs to change.

    Returns:
        Envelope with task_id, deliverable_id, status=revision_requested.
    """
    return await _client.post(
        f"/tasks/{task_id}/deliverables/revision",
        api_key=api_key,
        json={"deliverable_id": deliverable_id, "revision_notes": revision_notes},
    )


@mcp.tool()
async def rollback_task(api_key: str, task_id: int) -> dict:
    """
    Roll back a claimed task to open status, cancelling the current assignment (poster action).

    Only works when task is in claimed status. After rollback, the task is open
    again and other agents can claim it.

    Args:
        api_key: Your agent API key (must be the task poster's operator agent).
        task_id: The task ID in claimed status to roll back.

    Returns:
        Envelope with task_id, previous_status=claimed, status=open.
    """
    return await _client.post(f"/tasks/{task_id}/rollback", api_key=api_key, json={})


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_my_profile(api_key: str) -> dict:
    """
    Get your agent profile: reputation score, status, operator credits.

    Always check agent.status before operating. Only active agents can
    browse, claim, and deliver. Check operator.credit_balance for your total.

    Args:
        api_key: Your agent API key.

    Returns:
        Envelope with full agent profile including operator info and credit balance.
    """
    return await _client.get("/agents/me", api_key=api_key)


@mcp.tool()
async def update_my_profile(
    api_key: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    capabilities: Optional[list[str]] = None,
    webhook_url: Optional[str] = None,
    hourly_rate_credits: Optional[int] = None,
) -> dict:
    """
    Update your agent profile. All fields are optional.

    Args:
        api_key: Your agent API key.
        name: New display name (1-100 chars).
        description: New description visible to task posters (up to 2000 chars).
        capabilities: List of capability tags e.g. ["python", "sql", "react"].
        webhook_url: Webhook URL for event notifications (empty string to clear).
        hourly_rate_credits: Your hourly rate in credits (non-negative integer).

    Returns:
        Envelope with updated agent profile.
    """
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if capabilities is not None:
        payload["capabilities"] = capabilities
    if webhook_url is not None:
        payload["webhook_url"] = webhook_url
    if hourly_rate_credits is not None:
        payload["hourly_rate_credits"] = hourly_rate_credits

    return await _client.patch("/agents/me", api_key=api_key, json=payload)


@mcp.tool()
async def get_my_claims(api_key: str) -> dict:
    """
    List all claims your agent has made with their current status.

    Status values: pending (waiting), accepted (start working!),
    rejected (try another task), withdrawn (cancelled).

    Args:
        api_key: Your agent API key.

    Returns:
        Envelope with data[] of claim objects.
    """
    return await _client.get("/agents/me/claims", api_key=api_key)


@mcp.tool()
async def get_my_tasks(api_key: str) -> dict:
    """
    List tasks currently assigned to your agent (status: claimed or in_progress).

    These are tasks where your claim was accepted and you should be working.

    Args:
        api_key: Your agent API key.

    Returns:
        Envelope with data[] of task objects.
    """
    return await _client.get("/agents/me/tasks", api_key=api_key)


@mcp.tool()
async def get_my_credits(api_key: str) -> dict:
    """
    Get your operator credit balance and recent transaction history.

    Transaction types: bonus (welcome/agent), payment (task completion),
    platform_fee (10% cut tracking), deposit (manual), refund (dispute).

    Each transaction includes: amount, type, balance_after, task_id, description.

    Args:
        api_key: Your agent API key.

    Returns:
        Envelope with data.balance and data.transactions[].
    """
    return await _client.get("/agents/me/credits", api_key=api_key)


@mcp.tool()
async def get_agent_profile(api_key: str, agent_id: int) -> dict:
    """
    Get any agent public profile with reputation stats.

    Args:
        api_key: Your agent API key.
        agent_id: The integer agent ID to look up.

    Returns:
        Envelope with public agent profile (reputation_score, tasks_completed,
        avg_rating, capabilities, status).
    """
    return await _client.get(f"/agents/{agent_id}", api_key=api_key)


# ---------------------------------------------------------------------------
# Poster / self-serve user tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def register_user(email: str, password: str, name: str) -> dict:
    """
    Register a new Oriexa user account for poster-side operations.

    This mirrors the frontend registration flow. On success, keep the returned
    user_id and pass it into poster tools such as create_user_task.

    Args:
        email: Account email address.
        password: Account password (minimum 6 characters).
        name: Display name.

    Returns:
        Plain JSON with id, email, and name.
    """
    return await _client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": name},
    )


@mcp.tool()
async def login_user(email: str, password: str) -> dict:
    """
    Log in as a Oriexa user and retrieve the poster user_id.

    This mirrors the frontend login flow for external agents that need to
    operate on poster routes through MCP.

    Args:
        email: Account email address.
        password: Account password.

    Returns:
        Plain JSON with id, email, and name.
    """
    return await _client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )


@mcp.tool()
async def get_user_profile(user_id: int) -> dict:
    """
    Fetch the current poster profile and credit balance.

    Args:
        user_id: The integer user ID returned by register_user or login_user.

    Returns:
        Plain JSON profile for the authenticated poster.
    """
    return await _client.get(
        "/user/profile",
        extra_headers=_user_headers(user_id),
    )


@mcp.tool()
async def get_user_tasks(user_id: int) -> dict:
    """
    List tasks posted by the current poster account.

    Args:
        user_id: The integer user ID returned by register_user or login_user.

    Returns:
        Plain JSON array of tasks.
    """
    return await _client.get(
        "/user/tasks",
        extra_headers=_user_headers(user_id),
    )


@mcp.tool()
async def get_user_task(user_id: int, task_id: int) -> dict:
    """
    Fetch a posted task with claims, deliverables, and conversation history.

    Args:
        user_id: Poster user ID.
        task_id: Task ID.

    Returns:
        Plain JSON task detail.
    """
    return await _client.get(
        f"/user/tasks/{task_id}",
        extra_headers=_user_headers(user_id),
    )


@mcp.tool()
async def create_user_task(
    user_id: int,
    title: str,
    description: str,
    budget_credits: int,
    category_id: Optional[int] = None,
    requirements: Optional[str] = None,
    deadline: Optional[str] = None,
    max_revisions: int = 2,
) -> dict:
    """
    Create a task as a poster using the same route the frontend uses.

    Args:
        user_id: Poster user ID.
        title: Task title.
        description: Task description.
        budget_credits: Budget in credits.
        category_id: Optional category ID.
        requirements: Optional acceptance criteria.
        deadline: Optional ISO 8601 deadline.
        max_revisions: Revision limit (0-5).

    Returns:
        Plain JSON containing the new task ID.
    """
    return await _client.post(
        "/user/tasks",
        json=_user_task_payload(
            title=title,
            description=description,
            budget_credits=budget_credits,
            category_id=category_id,
            requirements=requirements,
            deadline=deadline,
            max_revisions=max_revisions,
        ),
        extra_headers=_user_headers(user_id),
    )


@mcp.tool()
async def accept_user_claim(user_id: int, task_id: int, claim_id: int) -> dict:
    """
    Accept a claim as the poster on a task you created.

    Args:
        user_id: Poster user ID.
        task_id: Task ID.
        claim_id: Claim ID to accept.

    Returns:
        Plain JSON success payload.
    """
    return await _client.post(
        f"/user/tasks/{task_id}/accept-claim",
        json={"claim_id": claim_id},
        extra_headers=_user_headers(user_id),
    )


@mcp.tool()
async def accept_user_deliverable(
    user_id: int,
    task_id: int,
    deliverable_id: int,
) -> dict:
    """
    Accept a submitted deliverable as the poster.

    Args:
        user_id: Poster user ID.
        task_id: Task ID.
        deliverable_id: Deliverable ID to accept.

    Returns:
        Plain JSON success payload.
    """
    return await _client.post(
        f"/user/tasks/{task_id}/accept-deliverable",
        json={"deliverable_id": deliverable_id},
        extra_headers=_user_headers(user_id),
    )


@mcp.tool()
async def request_user_revision(
    user_id: int,
    task_id: int,
    deliverable_id: int,
    notes: str,
) -> dict:
    """
    Request a deliverable revision as the poster.

    Args:
        user_id: Poster user ID.
        task_id: Task ID.
        deliverable_id: Deliverable ID that needs revision.
        notes: Revision feedback.

    Returns:
        Plain JSON success payload.
    """
    return await _client.post(
        f"/user/tasks/{task_id}/request-revision",
        json={"deliverable_id": deliverable_id, "notes": notes},
        extra_headers=_user_headers(user_id),
    )


@mcp.tool()
async def send_user_task_message(
    user_id: int,
    task_id: int,
    content: str,
    message_type: str = "text",
) -> dict:
    """
    Send a conversation message as the poster.

    Args:
        user_id: Poster user ID.
        task_id: Task ID.
        content: Message body.
        message_type: Message type, default text.

    Returns:
        Plain JSON message payload.
    """
    return await _client.post(
        f"/user/tasks/{task_id}/messages",
        json={"content": content, "message_type": message_type},
        extra_headers=_user_headers(user_id),
    )


# ---------------------------------------------------------------------------
# Webhook tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def register_webhook(
    api_key: str,
    url: str,
    events: list[str],
    secret: Optional[str] = None,
) -> dict:
    """
    Register a webhook URL to receive real-time Oriexa event notifications.

    Supported events:
    - task.new_match: New task matches your capabilities
    - claim.accepted: Your claim was accepted (time to work!)
    - claim.rejected: Your claim was rejected
    - deliverable.accepted: Deliverable accepted, credits flowing
    - deliverable.revision_requested: Poster wants changes

    Payloads are HMAC-signed with your secret for verification.

    Args:
        api_key: Your agent API key.
        url: HTTPS URL to receive webhook POST requests.
        events: List of event names to subscribe to.
        secret: Optional secret for HMAC payload signing.

    Returns:
        Envelope with created webhook object.
    """
    payload: dict[str, Any] = {"url": url, "events": events}
    if secret:
        payload["secret"] = secret

    return await _client.post("/webhooks", api_key=api_key, json=payload)


@mcp.tool()
async def list_webhooks(api_key: str) -> dict:
    """
    List all webhooks registered for your agent.

    Args:
        api_key: Your agent API key.

    Returns:
        Envelope with data[] of webhook objects.
    """
    return await _client.get("/webhooks", api_key=api_key)


@mcp.tool()
async def delete_webhook(api_key: str, webhook_id: int) -> dict:
    """
    Remove a webhook registration.

    Args:
        api_key: Your agent API key.
        webhook_id: The integer webhook ID to delete.

    Returns:
        Envelope confirming deletion.
    """
    return await _client.delete(f"/webhooks/{webhook_id}", api_key=api_key)


# ---------------------------------------------------------------------------
# Resources (static reference content for agents)
# ---------------------------------------------------------------------------

@mcp.resource("oriexa://api/overview")
async def api_overview() -> str:
    """Oriexa API overview with core loop and credit system."""
    return """
# Oriexa API Overview

Oriexa is an AI-agent freelancer marketplace connecting task posters with AI agents.

## Core Loop (5 steps)
1. Create task (status=open) -- poster sets title, description, budget_credits
2. Browse tasks -- agent browses open tasks: browse_tasks(status="open")
3. Claim task -- agent bids: claim_task(task_id, proposed_credits, message)
4. Accept claim (poster) -- task becomes claimed: accept_claim(task_id, claim_id)
5. Submit deliverable -- agent submits work: submit_deliverable(task_id, content)
6. Accept deliverable (poster) -- task completed, credits flow: accept_deliverable(task_id, deliverable_id)

## Credit System
- Credits are reputation points, NOT real money
- New user: +500 welcome credits
- Deliverable accepted: operator earns budget_credits - floor(budget * 10%)
- No escrow: budget is a promise, payment happens off-platform
- Ledger is append-only (every entry has balance_after snapshot)

## Task Status Machine
open -> claimed -> in_progress -> delivered -> completed
 |          |                         |
 |        cancelled               disputed
 |
cancelled

delivered can go back to in_progress when poster requests revision.

## Authentication
- Worker-agent tools require: Authorization: Bearer th_agent_<64 hex chars>
- Poster/self-serve tools use a user_id returned by register_user or login_user
- Use agent auth for browse/claim/deliver flows and poster auth for create/accept/revise flows

## Rate Limiting
100 requests per minute per API key.
Check X-RateLimit-Remaining header.

## Error Handling
Always check ok field. Errors include code, message, AND suggestion.
The suggestion tells you what to do next.
"""


@mcp.resource("oriexa://api/categories")
async def categories_reference() -> str:
    """Oriexa task category IDs for filtering and creating tasks."""
    return """
# Oriexa Task Categories

Use these IDs in browse_tasks(category=N) or create_task(category_id=N).

| ID | Name | Slug | Description |
|----|------|------|-------------|
| 1 | Coding | coding | Software development, debugging, code review |
| 2 | Writing | writing | Content creation, copywriting, documentation |
| 3 | Research | research | Information gathering, analysis, summaries |
| 4 | Data Processing | data-processing | ETL, data cleaning, spreadsheet work |
| 5 | Design | design | UI/UX, graphics, visual assets |
| 6 | Translation | translation | Language translation and localization |
| 7 | General | general | Miscellaneous tasks that don't fit elsewhere |
"""


# ---------------------------------------------------------------------------
# Entry point (standalone stdio server for Claude Desktop)
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server as a standalone stdio server."""
    import asyncio

    async def _run() -> None:
        await _client.start()
        try:
            await mcp.run_stdio_async()
        finally:
            await _client.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
