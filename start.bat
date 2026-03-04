@echo off
title Oriexa Backend
echo ---------------------------------------------------------
echo Oriexa Backend API
echo ---------------------------------------------------------

:: Activate Virtual Environment
if exist .venv\Scripts\activate (
    echo Activating virtual environment...
    call .venv\Scripts\activate
) else (
    echo [WARNING] .venv not found. Running with system python...
)

:: Run Migrations (includes orchestrator tables + SSE support)
echo Running database migrations...
alembic upgrade head
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Migrations failed. Attempting to stamp current revision...
    :: If tables already exist but alembic_version is missing/stale, stamp to latest
    alembic stamp head
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Could not recover. Check database connection and migration state.
        pause
        exit /b %ERRORLEVEL%
    )
    echo Stamped alembic to latest revision. Continuing...
)
echo Migrations complete.

:: Start Server
echo Starting API server on port 8000...
echo   SSE events endpoint: /api/v1/user/events/stream
echo   Progress stream:     /orchestrator/progress/executions/{id}/stream
uvicorn app.main:app --reload --port 8000
