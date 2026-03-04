"""FastAPI app factory with async lifespan, CORS, and router mounts."""

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.api_key import hash_api_key, is_valid_api_key_format
from app.auth.dependencies import AuthResponse
import logging

from app.config import settings
from app.db.engine import async_session, engine
from app.middleware.idempotency import (
    check_idempotency,
    complete_idempotency,
    fail_idempotency,
)
from app.middleware.rate_limit import add_rate_limit_headers, check_rate_limit, cleanup_expired
from app.routers import agents, auth, tasks, webhooks, user, meta
from app.api import health as orch_health, tasks as orch_tasks, agents as orch_agents
from app.api import webhooks as orch_webhooks, preview as orch_preview, dashboard as orch_dashboard
from app.api import progress as orch_progress
from app.api import events as orch_events
from app.observability.logger import setup_logging
from app.orchestrator.concurrency import WorkerPool
from app.orchestrator.task_picker import TaskPickerDaemon

orch_logger = logging.getLogger("app.orchestrator")


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

    # Validate mandatory deployment configuration
    _validate_deployment_config()

    # Start orchestrator daemon if API key is configured
    daemon = None
    if settings.ORIEXA_API_KEY:
        pool = WorkerPool(max_concurrent=settings.MAX_CONCURRENT_TASKS)
        daemon = TaskPickerDaemon(worker_pool=pool)
        await daemon.start()
        app.state.orchestrator_pool = pool
        app.state.orchestrator_daemon = daemon
        orch_logger.info("Orchestrator daemon started")
    else:
        orch_logger.warning("ORIEXA_API_KEY not set — orchestrator daemon disabled")

    # Start MCP session manager if available
    mcp_ctx = None
    try:
        from oriexa_mcp.server import mcp as mcp_server, _client as mcp_client
        await mcp_client.start()
        mcp_ctx = mcp_server.session_manager.run()
        await mcp_ctx.__aenter__()
        orch_logger.info("MCP session manager started")
    except ImportError:
        pass
    except Exception as e:
        orch_logger.warning(f"MCP session manager failed to start: {e}")

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
        await pool.shutdown()
        orch_logger.info("Orchestrator daemon stopped")

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
    """Handle Idempotency-Key for POST requests to /api/v1/* paths."""

    async def dispatch(self, request: Request, call_next):
        # Only apply to POST requests on /api/v1/* with an Idempotency-Key header
        if (
            request.method != "POST"
            or not request.url.path.startswith("/api/v1/")
            or "idempotency-key" not in request.headers
        ):
            return await call_next(request)

        # Extract agent info for idempotency (need agent_id from auth)
        auth_header = request.headers.get("authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        if not is_valid_api_key_format(token):
            # Let the normal auth flow handle invalid tokens
            return await call_next(request)

        key_hash = hash_api_key(token)
        idempotency_key = request.headers["idempotency-key"]

        # Read the body for hashing
        body = await request.body()
        body_text = body.decode("utf-8") if body else ""

        # Look up agent_id from the key hash
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
    from oriexa_mcp.server import mcp as mcp_server
    app.mount("/mcp", mcp_server.streamable_http_app())
    logging.getLogger("app.mcp").info("MCP server mounted at /mcp/")
except ImportError:
    logging.getLogger("app.mcp").warning("oriexa_mcp not installed — MCP endpoint disabled")

