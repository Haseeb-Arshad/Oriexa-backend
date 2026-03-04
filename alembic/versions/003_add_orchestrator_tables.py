"""Add orchestrator tables (orch_task_executions, orch_subtasks, orch_messages, orch_agent_runs)

Revision ID: 003
Revises: d5a56a9c08ca
Create Date: 2026-02-23
"""

from alembic import op

revision = "003"
down_revision = "d5a56a9c08ca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types (idempotent via DO $$ EXCEPTION handler)
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE orch_task_status AS ENUM "
        "('pending','claiming','clarifying','planning','executing',"
        "'reviewing','delivering','completed','failed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE agent_role AS ENUM "
        "('triage','clarification','planning','execution','complex_task','review'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE subtask_status AS ENUM "
        "('pending','in_progress','completed','failed','skipped'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE message_direction AS ENUM "
        "('agent_to_poster','poster_to_agent'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    # orch_task_executions
    op.execute(
        "CREATE TABLE IF NOT EXISTS orch_task_executions ("
        "id SERIAL PRIMARY KEY, "
        "oriexa_task_id INTEGER NOT NULL UNIQUE, "
        "status orch_task_status NOT NULL DEFAULT 'pending', "
        "task_snapshot JSONB NOT NULL DEFAULT '{}', "
        "graph_thread_id VARCHAR(255), "
        "workspace_path VARCHAR(500), "
        "total_tokens_used INTEGER NOT NULL DEFAULT 0, "
        "total_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0, "
        "error_message TEXT, "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "claimed_credits INTEGER, "
        "started_at TIMESTAMPTZ, "
        "completed_at TIMESTAMPTZ, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS orch_task_exec_status_idx ON orch_task_executions (status)")
    op.execute("CREATE INDEX IF NOT EXISTS orch_task_exec_task_id_idx ON orch_task_executions (oriexa_task_id)")

    # orch_subtasks
    op.execute(
        "CREATE TABLE IF NOT EXISTS orch_subtasks ("
        "id SERIAL PRIMARY KEY, "
        "execution_id INTEGER NOT NULL REFERENCES orch_task_executions(id), "
        "order_index INTEGER NOT NULL DEFAULT 0, "
        "title VARCHAR(500) NOT NULL, "
        "description TEXT NOT NULL, "
        "status subtask_status NOT NULL DEFAULT 'pending', "
        "result TEXT, "
        "files_changed JSONB NOT NULL DEFAULT '[]', "
        "depends_on JSONB NOT NULL DEFAULT '[]', "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS orch_subtasks_execution_id_idx ON orch_subtasks (execution_id)")

    # orch_messages
    op.execute(
        "CREATE TABLE IF NOT EXISTS orch_messages ("
        "id SERIAL PRIMARY KEY, "
        "execution_id INTEGER NOT NULL REFERENCES orch_task_executions(id), "
        "direction message_direction NOT NULL, "
        "content TEXT NOT NULL, "
        "deliverable_id INTEGER, "
        "thread_id VARCHAR(255), "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS orch_messages_execution_id_idx ON orch_messages (execution_id)")

    # orch_agent_runs
    op.execute(
        "CREATE TABLE IF NOT EXISTS orch_agent_runs ("
        "id SERIAL PRIMARY KEY, "
        "execution_id INTEGER NOT NULL REFERENCES orch_task_executions(id), "
        "role agent_role NOT NULL, "
        "model_used VARCHAR(200) NOT NULL, "
        "prompt_tokens INTEGER NOT NULL DEFAULT 0, "
        "completion_tokens INTEGER NOT NULL DEFAULT 0, "
        "duration_ms INTEGER NOT NULL DEFAULT 0, "
        "success BOOLEAN NOT NULL DEFAULT TRUE, "
        "error_message TEXT, "
        "input_summary TEXT, "
        "output_summary TEXT, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS orch_agent_runs_execution_id_idx ON orch_agent_runs (execution_id)")
    op.execute("CREATE INDEX IF NOT EXISTS orch_agent_runs_role_idx ON orch_agent_runs (role)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS orch_agent_runs")
    op.execute("DROP TABLE IF EXISTS orch_messages")
    op.execute("DROP TABLE IF EXISTS orch_subtasks")
    op.execute("DROP TABLE IF EXISTS orch_task_executions")
    op.execute("DROP TYPE IF EXISTS message_direction")
    op.execute("DROP TYPE IF EXISTS subtask_status")
    op.execute("DROP TYPE IF EXISTS agent_role")
    op.execute("DROP TYPE IF EXISTS orch_task_status")
