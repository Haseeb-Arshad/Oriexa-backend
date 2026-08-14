"""FastAPI app factory with async lifespan, CORS, and router mounts."""

import asyncio
import hashlib
import json
import re
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.errors import invalid_parameter_error
from app.auth.api_key import hash_api_key, is_valid_api_key_format
from app.auth.external_actor import decode_external_token
from app.auth.dependencies import AuthResponse
import logging

from app.config import settings
from app.db.engine import async_session, engine
from app.db.seed_agents import seed_agents
from app.middleware.idempotency import (
    check_idempotency,
    complete_idempotency,
    fail_idempotency,
)
from app.middleware.rate_limit import add_rate_limit_headers, check_rate_limit, cleanup_expired
from app.routers import agents, auth, external, tasks, webhooks, user, meta
from app.api import health as orch_health, tasks as orch_tasks, agents as orch_agents
from app.api import webhooks as orch_webhooks, preview as orch_preview, dashboard as orch_dashboard
from app.api import progress as orch_progress
from app.api import events as orch_events
from app.observability.logger import setup_logging
from app.orchestrator.concurrency import WorkerPool
from app.orchestrator.task_picker import TaskPickerDaemon
from app.orchestrator.reviewer_daemon import ReviewerDaemon

orch_logger = logging.getLogger("app.orchestrator")
TASK_ROUTE_RE = re.compile(r"^/api/v1/tasks/(?P<task_id>[^/]+)(?:/.*)?$")
TASK_ROUTE_RESERVED_SEGMENTS = {"bulk", "search"}


def _validate_deployment_config() -> None:
    """Log warnings for missing mandatory deployment configuration."""
    warnings = []
    if not settings.GITHUB_TOKEN:
        warnings.append("GITHUB_TOKEN is not set — GitHub deployment will fail for all tasks")
    if not settings.VERCEL_TOKEN and not settings.VERCEL_DEPLOY_ENDPOINT:
        warnings.append("Neither VERCEL_TOKEN nor VERCEL_DEPLOY_ENDPOINT is set — Vercel deployment will fail for all tasks")
    if not settings.GITHUB_ORG:
        warnings.append("GITHUB_ORG is not set — GitHub repos will be created in personal account")
    for w in warnings:
        orch_logger.warning("[DEPLOYMENT CONFIG] %s", w)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup structured logging
    setup_logging(level="INFO", json_output=settings.ENVIRONMENT != "development")

    # Startup: begin periodic rate-limit cleanup
    async def rate_limit_cleanup_loop():
        while True:
            await asyncio.sleep(60)
            cleanup_expired()

    cleanup_task = asyncio.create_task(rate_limit_cleanup_loop())

    # Seed system agents (idempotent — safe on every startup)
    try:
        async with async_session() as session:
            await seed_agents(session)
    except Exception as _seed_err:
        orch_logger.warning("Agent seeding failed (non-fatal): %s", _seed_err)

    # Validate mandatory deployment configuration
    _validate_deployment_config()

    # Start orchestrator daemon if API key is configured
    daemon = None
    reviewer_daemon = None
    if settings.ORIEXA_API_KEY:
        pool = WorkerPool(max_concurrent=settings.MAX_CONCURRENT_TASKS)
        daemon = TaskPickerDaemon(worker_pool=pool)
        reviewer_daemon = ReviewerDaemon(check_interval=int(settings.POLL_INTERVAL) if hasattr(settings, "POLL_INTERVAL") else 30)
        
        await daemon.start()
        await reviewer_daemon.start()
        
        app.state.orchestrator_pool = pool
        app.state.orchestrator_daemon = daemon
        app.state.reviewer_daemon = reviewer_daemon
        orch_logger.info("Orchestrator daemons started (TaskPicker, Reviewer)")
    else:
        orch_logger.warning("ORIEXA_API_KEY not set — orchestrator daemons disabled")

    # Start public MCP session managers if available
    mcp_ctx: AsyncExitStack | None = None
    mcp_client_started = False
    try:
        from oriexa_mcp.server import _client as mcp_client, external_mcp, public_mcp

        # Ensure the HTTP mounts have created their session managers before startup.
        public_mcp.streamable_http_app()
        external_mcp.streamable_http_app()

        await mcp_client.start()
        mcp_client_started = True
        mcp_ctx = AsyncExitStack()
        await mcp_ctx.__aenter__()
        await mcp_ctx.enter_async_context(public_mcp.session_manager.run())
        await mcp_ctx.enter_async_context(external_mcp.session_manager.run())
        orch_logger.info("Public MCP session managers started")
    except ImportError:
        pass
    except Exception as e:
        orch_logger.warning("MCP session managers failed to start: %s", e)
        if mcp_ctx is not None:
            try:
                await mcp_ctx.__aexit__(type(e), e, e.__traceback__)
            except Exception:
                pass
            mcp_ctx = None
        if mcp_client_started:
            try:
                await mcp_client.close()
            except Exception:
                pass

    yield

    # Shutdown MCP
    if mcp_ctx:
        try:
            from oriexa_mcp.server import _client as mcp_client
            await mcp_ctx.__aexit__(None, None, None)
            await mcp_client.close()
        except Exception:
            pass

    # Shutdown orchestrator
    if daemon:
        await daemon.stop()
        if reviewer_daemon:
            await reviewer_daemon.stop()
        await pool.shutdown()
        orch_logger.info("Orchestrator daemons stopped")

    # Shutdown: cancel cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Dispose SQLAlchemy engine (close all pooled connections)
    await engine.dispose()
    orch_logger.info("Database engine disposed")


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Handle Idempotency-Key for agent and external actor POST routes."""

    async def dispatch(self, request: Request, call_next):
        # Only apply to POST requests on API routes with an Idempotency-Key header
        if (
            request.method != "POST"
            or (
                not request.url.path.startswith("/api/v1/")
                and not request.url.path.startswith("/api/v2/external/")
            )
            or "idempotency-key" not in request.headers
        ):
            return await call_next(request)

        # Extract actor info for idempotency (needs agent_id from auth)
        auth_header = request.headers.get("authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        key_hash = ""
        agent_id = None

        if is_valid_api_key_format(token):
            key_hash = hash_api_key(token)
        elif request.url.path.startswith("/api/v2/external/"):
            try:
                payload = decode_external_token(token)
                agent_id = int(payload["agent_id"])
                key_hash = hashlib.sha256(token.encode()).hexdigest()
            except Exception:
                # Let the normal auth flow handle invalid tokens
                return await call_next(request)
        else:
            return await call_next(request)

        idempotency_key = request.headers["idempotency-key"]

        # Read the body for hashing
        body = await request.body()
        body_text = body.decode("utf-8") if body else ""

        if agent_id is None:
            # Look up agent_id from the th_agent_* key hash
            from sqlalchemy import select
            from app.db.models import Agent

            async with async_session() as session:
                result = await session.execute(
                    select(Agent.id).where(Agent.api_key_hash == key_hash).limit(1)
                )
                agent_row = result.first()
                if not agent_row:
                    return await call_next(request)
                agent_id = agent_row.id

        async with async_session() as session:
            path = request.url.path
            idem_result = await check_idempotency(session, agent_id, idempotency_key, path, body_text)
            await session.commit()

        if idem_result.action == "replay":
            # Add rate limit headers to replayed response
            rl = check_rate_limit(key_hash)
            # Don't increment again — already incremented by auth dependency
            resp = idem_result.response
            return add_rate_limit_headers(resp, rl)

        if idem_result.action == "error":
            return idem_result.response

        # action == "proceed" — execute the handler
        try:
            response = await call_next(request)
            # Cache the response for replay
            resp_body = b""
            async for chunk in response.body_iterator:
                if isinstance(chunk, bytes):
                    resp_body += chunk
                else:
                    resp_body += chunk.encode("utf-8")

            cached_response = JSONResponse(
                content=json.loads(resp_body),
                status_code=response.status_code,
            )
            # Copy headers
            for k, v in response.headers.items():
                if k.lower() not in ("content-length", "content-type"):
                    cached_response.headers[k] = v

            async with async_session() as session:
                await complete_idempotency(session, idem_result.record_id, cached_response)
                await session.commit()

            return cached_response

        except Exception:
            async with async_session() as session:
                await fail_idempotency(session, idem_result.record_id)
                await session.commit()
            raise


app = FastAPI(
    title="Oriexa API",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware (order matters: last added = first executed)
app.add_middleware(IdempotencyMiddleware)

# CORS
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handler for AuthResponse (auth middleware returns responses via exception)
@app.exception_handler(AuthResponse)
async def auth_response_handler(request: Request, exc: AuthResponse):
    return exc.response


def _invalid_task_id_response(path: str) -> JSONResponse | None:
    match = TASK_ROUTE_RE.match(path)
    if not match:
        return None

    task_id = match.group("task_id")
    if task_id in TASK_ROUTE_RESERVED_SEGMENTS:
        return None

    if not task_id.isdigit() or int(task_id) < 1:
        return invalid_parameter_error(
            f"Invalid task ID: {task_id}",
            "Task IDs are positive integers. Use GET /api/v1/tasks to browse available tasks.",
        )

    return None


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        invalid_task_id = _invalid_task_id_response(request.url.path)
        if invalid_task_id is not None:
            return invalid_task_id

    return await http_exception_handler(request, exc)


# Health check
@app.get("/health")
async def health():
    return {"status": "ok"}


# Mount routers
app.include_router(auth.router, prefix="/api/auth")
app.include_router(tasks.router, prefix="/api/v1/tasks")
app.include_router(agents.router, prefix="/api/v1/agents")
app.include_router(webhooks.router, prefix="/api/v1/webhooks")
app.include_router(user.router, prefix="/api/v1/user")
app.include_router(meta.router, prefix="/api/v1/meta")
app.include_router(external.router)

# Orchestrator routers
app.include_router(orch_health.router, prefix="/orchestrator", tags=["orchestrator"])
app.include_router(orch_tasks.router, tags=["orchestrator"])
app.include_router(orch_agents.router, tags=["orchestrator"])
app.include_router(orch_webhooks.router, tags=["orchestrator"])
app.include_router(orch_preview.router, tags=["preview"])
app.include_router(orch_progress.router, tags=["progress"])
app.include_router(orch_events.router, tags=["events"])
app.include_router(orch_dashboard.router, tags=["dashboard"])

# MCP server (streamable HTTP) — mounted at /mcp/
try:
    from oriexa_mcp.server import external_mcp, public_mcp
    public_mcp.settings.streamable_http_path = "/"
    external_mcp.settings.streamable_http_path = "/"
    app.mount("/mcp/v2", external_mcp.streamable_http_app())
    app.mount("/mcp", public_mcp.streamable_http_app())
    logging.getLogger("app.mcp").info("MCP servers mounted at /mcp and /mcp/v2")
except ImportError:
    logging.getLogger("app.mcp").warning("oriexa_mcp not installed — MCP endpoint disabled")

