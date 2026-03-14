"""Reconcile schema drift for external v2 rollout.

Revision ID: 007
Revises: 006
Create Date: 2026-03-14
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
            )
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return bool(result.scalar())


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE user_role AS ENUM ('poster', 'operator', 'both', 'admin'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    if not _column_exists("users", "role"):
        op.execute(
            "ALTER TABLE users "
            "ADD COLUMN role user_role NOT NULL DEFAULT 'both'"
        )

    if not _column_exists("tasks", "agent_remarks"):
        op.execute(
            "ALTER TABLE tasks "
            "ADD COLUMN agent_remarks JSONB NOT NULL DEFAULT '[]'"
        )

    op.execute("ALTER TYPE webhook_event ADD VALUE IF NOT EXISTS 'task.updated'")
    op.execute("ALTER TYPE webhook_event ADD VALUE IF NOT EXISTS 'claim.created'")
    op.execute("ALTER TYPE webhook_event ADD VALUE IF NOT EXISTS 'deliverable.submitted'")
    op.execute("ALTER TYPE webhook_event ADD VALUE IF NOT EXISTS 'message.created'")
    op.execute("ALTER TYPE task_msg_type ADD VALUE IF NOT EXISTS 'evaluation'")

    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE orch_task_status AS ENUM "
        "('pending','claiming','clarifying','planning','executing','reviewing','delivering','completed','failed'); "
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

    op.execute(
        "CREATE TABLE IF NOT EXISTS task_messages ("
        "id SERIAL PRIMARY KEY, "
        "task_id INTEGER NOT NULL REFERENCES tasks(id), "
        "sender_type task_msg_sender_type NOT NULL, "
        "sender_id INTEGER, "
        "sender_name VARCHAR(255) NOT NULL, "
        "content TEXT NOT NULL, "
        "message_type task_msg_type NOT NULL DEFAULT 'text', "
        "structured_data JSONB, "
        "parent_id INTEGER REFERENCES task_messages(id), "
        "claim_id INTEGER REFERENCES task_claims(id), "
        "is_read BOOLEAN NOT NULL DEFAULT FALSE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    op.execute("CREATE INDEX IF NOT EXISTS task_messages_task_id_idx ON task_messages (task_id)")
    op.execute("CREATE INDEX IF NOT EXISTS task_messages_task_created_idx ON task_messages (task_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS task_messages_parent_id_idx ON task_messages (parent_id)")


def downgrade() -> None:
    # This migration reconciles drift and extends enums. Downgrade is intentionally a no-op.
    pass
