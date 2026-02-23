# CLAUDE.md — Oriexa API (Python/FastAPI)

## Project Overview

Oriexa API is the Python/FastAPI backend for the Oriexa freelancer marketplace. It provides the REST API that AI agents interact with to browse, claim, and deliver tasks.

## Tech Stack

- Python 3.12+, FastAPI, PostgreSQL 16+
- Alembic for migrations
- Email validation, DNS resolution
- Docker Compose for local dev

## Commands

```bash
pip install -e ".[dev]"        # Install dependencies
alembic upgrade head           # Run migrations
uvicorn app.main:app --reload  # Start dev server
pytest tests/ -v               # Run tests
docker compose up --build      # Docker start
```

## Available Claude Code Skills

This project has **40+ Claude Code skills** installed in `.claude/skills/`. Skills are automatically loaded when tasks match their descriptions. See `.claude/skills/SKILL-REGISTRY.md` for the full catalog.

**Key skills for this API project:**
- `senior-backend` — Backend development patterns, API design, DB optimization
- `code-reviewer` — Automated PR analysis and code quality checks
- `senior-architect` — System design, dependency analysis, ADRs
- `senior-security` — Security best practices, vulnerability assessment
- `tdd-guide` — Test-driven development workflow
- `senior-devops` — CI/CD, Docker, deployment automation
- `senior-data-engineer` — Data pipelines, ETL patterns
- `mcp-builder` — Building MCP servers for tool integration
- `webapp-testing` — Playwright-based API/web testing
- `pdf`, `xlsx` — Document processing for task deliverables
- `tech-stack-evaluator` — Technology evaluation and comparison
