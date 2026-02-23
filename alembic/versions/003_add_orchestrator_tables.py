"""Add orchestrator tables (orch_task_executions, orch_subtasks, orch_messages, orch_agent_runs)

Revision ID: 003
Revises: d5a56a9c08ca
Create Date: 2026-02-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "d5a56a9c08ca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create orchestrator enum types
    op.execute(
        "CREATE TYPE orch_task_status AS ENUM "
        "('pending', 'claiming', 'clarifying', 'planning', 'executing', "
        "'reviewing', 'delivering', 'completed', 'failed')"
    )
    op.execute(
        "CREATE TYPE agent_role AS ENUM "
        "('triage', 'clarification', 'planning', 'execution', 'complex_task', 'review')"
    )
    op.execute(
        "CREATE TYPE subtask_status AS ENUM "
        "('pending', 'in_progress', 'completed', 'failed', 'skipped')"
    )
    op.execute(
        "CREATE TYPE message_direction AS ENUM "
        "('agent_to_poster', 'poster_to_agent')"
    )

    # orch_task_executions
    op.create_table(
        "orch_task_executions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("oriexa_task_id", sa.Integer(), nullable=False, unique=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "claiming", "clarifying", "planning", "executing",
                "reviewing", "delivering", "completed", "failed",
                name="orch_task_status", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("task_snapshot", JSONB, nullable=False, server_default="{}"),
        sa.Column("graph_thread_id", sa.String(255), nullable=True),
        sa.Column("workspace_path", sa.String(500), nullable=True),
        sa.Column("total_tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_credits", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("orch_task_exec_status_idx", "orch_task_executions", ["status"])
    op.create_index("orch_task_exec_task_id_idx", "orch_task_executions", ["oriexa_task_id"])

    # orch_subtasks
    op.create_table(
        "orch_subtasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("orch_task_executions.id"), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "in_progress", "completed", "failed", "skipped",
                name="subtask_status", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("files_changed", JSONB, nullable=False, server_default="[]"),
        sa.Column("depends_on", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("orch_subtasks_execution_id_idx", "orch_subtasks", ["execution_id"])

    # orch_messages
    op.create_table(
        "orch_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("orch_task_executions.id"), nullable=False),
        sa.Column(
            "direction",
            sa.Enum(
                "agent_to_poster", "poster_to_agent",
                name="message_direction", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("deliverable_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("orch_messages_execution_id_idx", "orch_messages", ["execution_id"])

    # orch_agent_runs
    op.create_table(
        "orch_agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.Integer(), sa.ForeignKey("orch_task_executions.id"), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "triage", "clarification", "planning", "execution", "complex_task", "review",
                name="agent_role", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("model_used", sa.String(200), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("orch_agent_runs_execution_id_idx", "orch_agent_runs", ["execution_id"])
    op.create_index("orch_agent_runs_role_idx", "orch_agent_runs", ["role"])


def downgrade() -> None:
    op.drop_table("orch_agent_runs")
    op.drop_table("orch_messages")
    op.drop_table("orch_subtasks")
    op.drop_table("orch_task_executions")

    op.execute("DROP TYPE IF EXISTS message_direction")
    op.execute("DROP TYPE IF EXISTS subtask_status")
    op.execute("DROP TYPE IF EXISTS agent_role")
    op.execute("DROP TYPE IF EXISTS orch_task_status")
