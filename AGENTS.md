# Oriexa API Agent Manual

## Identity

`oriexa-api/` is the active backend and agent-runtime surface for Oriexa. It owns:

- FastAPI REST endpoints
- authentication, rate limiting, idempotency, and DB state transitions
- MCP tools and transports
- the reviewer agent
- the orchestrator and its prompt pipeline

## Mission

Work here when the task touches any authoritative backend behavior, including:

- task, claim, deliverable, review, or webhook semantics
- API envelope, auth, rate limits, or pagination
- MCP tool behavior or transport configuration
- reviewer/orchestrator agent execution
- runtime prompt files in `prompts/`

## Scope

Primary areas:

- `app/main.py` - unified FastAPI app
- `main.py` - compatibility launcher that runs the unified app on port 8000
- `app/routers/` - REST endpoints
- `app/schemas/` - request/response models
- `app/db/` - SQLAlchemy engine and models
- `app/middleware/` - rate limiting and idempotency
- `app/services/` - credits, crypto, auth, webhooks
- `app/orchestrator/` - supervisor, worker pool, task picker, lifecycle
- `app/agents/` - triage/planning/execution/review agents
- `prompts/` - system prompts for orchestrator stages
- `oriexa_mcp/` - MCP tool wrapper and transport logic
- `tests/` - backend test suite
- `test_mcp_e2e.py` - full MCP functional verification
- `scripts/test_mcp_transports.py` - MCP transport smoke checks

## Non-Goals

Do not treat this repo as the main place for product UI work. If the change is primarily visual or dashboard-focused, start in `../Oriexa/`.

Do not change public contract behavior here without checking the frontend callers and skill files in `../Oriexa/`.

## Read Order

Read these before large changes:

1. workspace `AGENTS.md`
2. this file
3. `README.md`
4. `CLAUDE.md`
5. the exact router, schema, MCP, or orchestrator modules you will edit

## Recommended Local Run

For the unified local stack used by the frontend and MCP tests, prefer:

```bash
python main.py
```

or:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Some older docs still mention running `app.main` on `8001`. For current cross-project work, `8000` is the least confusing choice because:

- the frontend default points to `http://localhost:8000`
- MCP transport checks in this workspace use the unified app on `8000`

## System Model

The safest mental model for this repo is:

- `app/routers/` defines the REST contract
- `oriexa_mcp/` exposes the same operations as MCP tools
- `prompts/` shape orchestrator reasoning
- `tests/` and `test_mcp_e2e.py` are the contract guardrails

If you change behavior in one of those layers, inspect the others before finishing.

## Critical Invariants

- Response envelope stays consistent: success and error shapes must remain predictable.
- Agent auth stays Bearer-token based with `th_agent_` keys.
- Public entity IDs remain integers.
- Deliverable revision numbering starts at `1`, not `0`.
- The task search route must remain distinct from task-id routing.
- MCP streamable HTTP must be reachable at `/mcp` (and `/mcp/`).
- Stdio MCP must work via `python -m oriexa_mcp.server`.
- MCP configs must use `ORIEXA_API_BASE_URL` that includes `/api/v1`.

## Files Of Record

Use these as primary entry files:

- `app/main.py` - app composition, router inclusion, MCP mount
- `app/routers/tasks.py` - core marketplace state transitions
- `app/routers/agents.py`
- `app/routers/webhooks.py`
- `app/config.py`
- `oriexa_mcp/server.py`
- `prompts/triage.md`
- `prompts/planning.md`
- `prompts/execution.md`
- `tests/test_tasks.py`
- `tests/test_webhooks.py`
- `test_mcp_e2e.py`

## Change Rules

When changing REST behavior:

1. update router logic
2. update schemas if request/response shapes changed
3. update tests
4. update MCP wrappers if the same operation is exposed there
5. update frontend callers and skill docs in `../Oriexa/` when the contract changed

When changing MCP behavior:

1. update `oriexa_mcp/server.py`
2. verify HTTP transport at `/mcp`
3. verify stdio transport with `python -m oriexa_mcp.server`
4. update `.mcp.json`, `claude_desktop_config.json`, and `README.md` if startup or config changed

When changing orchestrator behavior:

1. inspect the relevant prompt file in `prompts/`
2. inspect the matching agent implementation in `app/agents/` or `app/orchestrator/`
3. update tests for the execution path you changed

## Verification

Use these commands from `oriexa-api/`:

- `python -m py_compile app\\main.py app\\routers\\tasks.py oriexa_mcp\\server.py`
- `pytest tests -v`
- `python -X utf8 test_mcp_e2e.py --next-url http://127.0.0.1:8000`
- `python scripts/test_mcp_transports.py`

Use narrower tests when possible:

- router or contract changes: `pytest tests/test_tasks.py -v`
- auth changes: `pytest tests/test_auth.py -v`
- webhook changes: `pytest tests/test_webhooks.py -v`
- orchestrator changes: `pytest tests/test_supervisor.py tests/test_task_picker.py -v`

## Common Traps

- Do not trust stale port references without checking the actual frontend caller.
- Do not change MCP paths casually; clients depend on `/mcp`.
- Do not update only the REST layer and forget MCP parity.
- Do not update MCP parity and forget README/config examples.
- Do not assume prompt changes are safe without re-running orchestrator-adjacent tests.

## Done Criteria

Backend work is done when:

- the authoritative router or MCP behavior is correct
- contract consumers remain aligned
- relevant tests pass
- MCP still works for both HTTP and stdio when applicable
