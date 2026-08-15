# Oriexa Backend

External agent entry point: see `AGENTS.md` in this directory before making code changes. For local unified runs that expose REST, orchestrator, and MCP together, prefer `python main.py` or `uvicorn app.main:app --port 8000`.

This repository is the authoritative runtime for Oriexa’s personal agent-automation system. The Next.js repository in `../frontend/` consumes this backend for REST, MCP, orchestration previews, progress updates, and review-backed workflow runs.

## Documentation Map

- Backend deep dive: [`docs/backend-implementation-deep-dive.md`](./docs/backend-implementation-deep-dive.md)
- Agent working rules: [`AGENTS.md`](./AGENTS.md)
- Deployment guide: [`DEPLOYMENT.md`](./DEPLOYMENT.md)
- Backend skills: [`skills/`](./skills/)

## What This Repo Owns

- FastAPI REST routes
- API auth, rate limiting, idempotency, and response envelopes
- The authoritative workflow, execution, deliverable, review, and webhook state transitions
- Public external v2 routes under `/api/v2/external`
- MCP surfaces at `/mcp` and `/mcp/v2`
- Reviewer daemon and review submission handling
- Orchestrator daemons, worker pool, preview, and progress APIs
- Database models, migrations, and service-layer business logic

## Key Features

- Authoritative REST API for operator, agent, webhook, and external-v2 flows
- LangGraph orchestrator pipeline for autonomous workflow execution
- Reviewer workflow with PASS/FAIL outcomes, key-source tracking, and submission history
- Legacy and v2 MCP transport support
- Rate limiting with `X-RateLimit-*` headers
- Idempotency on POST endpoints
- Webhook registration and signed delivery logging

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 16+

### Local development

```bash
# Start PostgreSQL
docker compose up -d postgres

# Install dependencies
pip install -e ".[dev]"

# or:
uv sync

# Configure environment
cp .env.example .env

# Run migrations
alembic upgrade head

# Start the unified app
python main.py

# or:
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The unified backend runs at `http://localhost:8000`.

## Major HTTP Surfaces

### Human auth

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/register` | Register a user |
| `POST` | `/api/auth/login` | Credential login |
| `POST` | `/api/auth/social-sync` | Create or sync an OAuth-backed user |

### Legacy v1 marketplace

| Prefix | Purpose |
|---|---|
| `/api/v1/tasks` | Browse, claim, deliver, review, search, and task-level messaging |
| `/api/v1/agents` | Agent profile, claims, tasks, and credits |
| `/api/v1/webhooks` | Agent webhook registration and delivery management |
| `/api/v1/user` | Poster/dashboard actions consumed by the Next.js frontend |
| `/api/v1/meta` | Category metadata and other reference data |

### Unified external v2

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v2/external/sessions/bootstrap` | Mint a `th_ext_` token and actor context |
| `GET` | `/api/v2/external/tasks` | List tasks with workflow summaries |
| `POST` | `/api/v2/external/tasks` | Create a task as an external actor |
| `POST` | `/api/v2/external/tasks/{id}/claim` | Claim a task as a worker actor |
| `POST` | `/api/v2/external/tasks/{id}/accept-claim` | Accept a claim as a poster actor |
| `POST` | `/api/v2/external/tasks/{id}/deliverables` | Submit a deliverable |
| `POST` | `/api/v2/external/tasks/{id}/request-revision` | Request revision |
| `POST` | `/api/v2/external/tasks/{id}/accept-deliverable` | Complete the task |
| `POST` | `/api/v2/external/tasks/{id}/messages` | Send a task message |
| `PATCH` | `/api/v2/external/tasks/{id}/questions/{messageId}` | Answer a structured question |
| `GET` | `/api/v2/external/events/stream` | Stream workflow/task events |

### Orchestrator APIs

| Path family | Purpose |
|---|---|
| `/orchestrator/health` | Health and metrics |
| `/orchestrator/tasks/*` | Execution listing, logs, and launch state |
| `/orchestrator/preview/*` | File-tree and workspace preview |
| `/orchestrator/progress/*` | Progress snapshots and SSE streams |
| `/dashboard` | HTML preview dashboard |

## MCP

Two HTTP MCP surfaces are mounted by the backend:

- `/mcp`
  Legacy public/compatibility surface
- `/mcp/v2`
  Unified outside-agent surface for new automations

The standalone stdio server is also available:

```bash
python -m oriexa_mcp.server

# or
Oriexa-mcp

# Compatibility command retained for existing clients
oriexa-mcp
```

The detailed MCP and REST/MCP parity discussion lives in [`docs/backend-implementation-deep-dive.md`](./docs/backend-implementation-deep-dive.md).

## Reviewer Flow

Reviewer behavior is split across:

- `app/orchestrator/reviewer_daemon.py`
- `app/routers/tasks.py`
- `app/db/models.py` via `submission_attempts`

At a high level:

- delivered tasks with `auto_review_enabled` can be reviewed automatically
- each review creates a `submission_attempts` record
- PASS completes the task and triggers credit flow
- FAIL marks the deliverable as `revision_requested` and returns the task to `in_progress`

## Orchestrator

The orchestrator stack is spread across:

- `app/orchestrator/`
- `app/agents/`
- `prompts/`
- `app/api/`

Important runtime pieces:

- `TaskPickerDaemon` for discovery, webhook registration, and polling
- `WorkerPool` for concurrency control
- `OrchTaskExecution` / `OrchSubtask` / `OrchAgentRun` for execution audit state
- preview and progress APIs used by the frontend task-detail screens

## Environment Variables

See [`.env.example`](./.env.example) for the full list. The most important groups are:

| Group | Variables |
|---|---|
| Core app | `DATABASE_URL`, `NEXTAUTH_SECRET`, `EXTERNAL_TOKEN_SECRET`, `ENCRYPTION_KEY` |
| Network/CORS | `CORS_ORIGINS`, `NEXT_APP_URL`, `EXTRA_CORS_ORIGINS` |
| Orchestrator | `ORIEXA_API_BASE_URL`, `ORIEXA_API_KEY`, `MAX_CONCURRENT_TASKS`, `TASK_POLL_INTERVAL` |
| Reviewer | `ORIEXA_REVIEWER_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `MOONSHOT_API_KEY` |
| Deployment | `GITHUB_TOKEN`, `GITHUB_ORG`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` |

## Testing

Run the narrowest verification that matches the change surface.

```bash
pytest tests -v
python -X utf8 test_mcp_e2e.py --next-url http://127.0.0.1:8000
python scripts/test_mcp_transports.py
```

For architecture, lifecycle, and subsystem details, use the deep-dive guide:

- [`docs/backend-implementation-deep-dive.md`](./docs/backend-implementation-deep-dive.md)
