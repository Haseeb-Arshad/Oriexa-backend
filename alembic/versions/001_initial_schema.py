"""Initial schema — all 12 tables + enums + indexes

Revision ID: 001
Revises:
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create all PostgreSQL enum types
    op.execute("CREATE TYPE user_role AS ENUM ('poster', 'operator', 'both', 'admin')")
    op.execute("CREATE TYPE agent_status AS ENUM ('active', 'paused', 'suspended')")
    op.execute(
        "CREATE TYPE task_status AS ENUM "
        "('open', 'claimed', 'in_progress', 'delivered', 'completed', 'cancelled', 'disputed')"
    )
    op.execute("CREATE TYPE claim_status AS ENUM ('pending', 'accepted', 'rejected', 'withdrawn')")
    op.execute(
        "CREATE TYPE deliverable_status AS ENUM "
        "('submitted', 'accepted', 'rejected', 'revision_requested')"
    )
    op.execute(
        "CREATE TYPE transaction_type AS ENUM "
        "('deposit', 'bonus', 'payment', 'platform_fee', 'refund')"
    )
    op.execute(
        "CREATE TYPE webhook_event AS ENUM "
        "('task.new_match', 'claim.accepted', 'claim.rejected', "
        "'deliverable.accepted', 'deliverable.revision_requested')"
    )
    op.execute("CREATE TYPE llm_provider AS ENUM ('openrouter', 'openai', 'anthropic')")
    op.execute("CREATE TYPE review_result AS ENUM ('pass', 'fail', 'pending', 'skipped')")
    op.execute("CREATE TYPE review_key_source AS ENUM ('poster', 'freelancer', 'none')")

    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("poster", "operator", "both", "admin", name="user_role", create_type=False),
            nullable=False,
            server_default="both",
        ),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column("credit_balance", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # 2. categories
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    )

    # 3. agents
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("operator_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("capabilities", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("category_ids", ARRAY(sa.Integer), nullable=False, server_default="{}"),
        sa.Column("hourly_rate_credits", sa.Integer, nullable=True),
        sa.Column("api_key_hash", sa.String(64), nullable=True),
        sa.Column("api_key_prefix", sa.String(14), nullable=True),
        sa.Column("webhook_url", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "paused", "suspended", name="agent_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("reputation_score", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("tasks_completed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_rating", sa.Float, nullable=True),
        sa.Column("freelancer_llm_key_encrypted", sa.Text, nullable=True),
        sa.Column(
            "freelancer_llm_provider",
            sa.Enum("openrouter", "openai", "anthropic", name="llm_provider", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("agents_operator_id_idx", "agents", ["operator_id"])
    op.create_index("agents_status_idx", "agents", ["status"])

    # 4. tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("poster_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("requirements", sa.Text, nullable=True),
        sa.Column("budget_credits", sa.Integer, nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open", "claimed", "in_progress", "delivered", "completed",
                "cancelled", "disputed",
                name="task_status", create_type=False,
            ),
            nullable=False,
            server_default="open",
        ),
        sa.Column("claimed_by_agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_revisions", sa.Integer, nullable=False, server_default="2"),
        sa.Column("auto_review_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("poster_llm_key_encrypted", sa.Text, nullable=True),
        sa.Column(
            "poster_llm_provider",
            sa.Enum("openrouter", "openai", "anthropic", name="llm_provider", create_type=False),
            nullable=True,
        ),
        sa.Column("poster_max_reviews", sa.Integer, nullable=True),
        sa.Column("poster_reviews_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("tasks_status_idx", "tasks", ["status"])
    op.create_index("tasks_poster_id_idx", "tasks", ["poster_id"])
    op.create_index("tasks_category_id_idx", "tasks", ["category_id"])
    op.create_index("tasks_created_at_idx", "tasks", ["created_at"])

    # 5. task_claims
    op.create_table(
        "task_claims",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("proposed_credits", sa.Integer, nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "accepted", "rejected", "withdrawn",
                name="claim_status", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("task_claims_task_id_idx", "task_claims", ["task_id"])
    op.create_index("task_claims_agent_id_idx", "task_claims", ["agent_id"])
    op.create_index(
        "task_claims_task_agent_status_idx", "task_claims", ["task_id", "agent_id", "status"]
    )

    # 6. deliverables
    op.create_table(
        "deliverables",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "submitted", "accepted", "rejected", "revision_requested",
                name="deliverable_status", create_type=False,
            ),
            nullable=False,
            server_default="submitted",
        ),
        sa.Column("revision_notes", sa.Text, nullable=True),
        sa.Column("revision_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("deliverables_task_id_idx", "deliverables", ["task_id"])
    op.create_index("deliverables_task_agent_idx", "deliverables", ["task_id", "agent_id"])

    # 7. reviews
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("reviewer_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("quality_score", sa.Integer, nullable=True),
        sa.Column("speed_score", sa.Integer, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_unique_constraint("reviews_task_id_unique", "reviews", ["task_id"])

    # 8. credit_transactions
    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "deposit", "bonus", "payment", "platform_fee", "refund",
                name="transaction_type", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("counterparty_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("balance_after", sa.Integer, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("credit_transactions_user_id_idx", "credit_transactions", ["user_id"])
    op.create_index("credit_transactions_created_at_idx", "credit_transactions", ["created_at"])

    # 9. webhooks
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("secret", sa.String(64), nullable=False),
        sa.Column(
            "events",
            ARRAY(
                sa.Enum(
                    "task.new_match", "claim.accepted", "claim.rejected",
                    "deliverable.accepted", "deliverable.revision_requested",
                    name="webhook_event", create_type=False,
                )
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("webhooks_agent_id_idx", "webhooks", ["agent_id"])

    # 10. webhook_deliveries
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("webhook_id", sa.Integer, sa.ForeignKey("webhooks.id"), nullable=False),
        sa.Column(
            "event",
            sa.Enum(
                "task.new_match", "claim.accepted", "claim.rejected",
                "deliverable.accepted", "deliverable.revision_requested",
                name="webhook_event", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("success", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("duration_ms", sa.Integer, nullable=True),
    )
    op.create_index("webhook_deliveries_webhook_id_idx", "webhook_deliveries", ["webhook_id"])
    op.create_index("webhook_deliveries_attempted_at_idx", "webhook_deliveries", ["attempted_at"])

    # 11. idempotency_keys
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_path", sa.String(500), nullable=False),
        sa.Column("request_body_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column(
            "locked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_unique_constraint(
        "idempotency_keys_agent_key_ux", "idempotency_keys", ["agent_id", "idempotency_key"]
    )
    op.create_index("idempotency_keys_expires_at_idx", "idempotency_keys", ["expires_at"])

    # 12. submission_attempts
    op.create_table(
        "submission_attempts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("agent_id", sa.Integer, sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("deliverable_id", sa.Integer, sa.ForeignKey("deliverables.id"), nullable=True),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "review_result",
            sa.Enum("pass", "fail", "pending", "skipped", name="review_result", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("review_feedback", sa.Text, nullable=True),
        sa.Column("review_scores", JSONB, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "review_key_source",
            sa.Enum("poster", "freelancer", "none", name="review_key_source", create_type=False),
            nullable=False,
            server_default="none",
        ),
        sa.Column("llm_model_used", sa.String(200), nullable=True),
    )
    op.create_index("submission_attempts_task_id_idx", "submission_attempts", ["task_id"])
    op.create_index("submission_attempts_agent_id_idx", "submission_attempts", ["agent_id"])
    op.create_index(
        "submission_attempts_task_agent_idx", "submission_attempts", ["task_id", "agent_id"]
    )


def downgrade() -> None:
    op.drop_table("submission_attempts")
    op.drop_table("idempotency_keys")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_table("credit_transactions")
    op.drop_table("reviews")
    op.drop_table("deliverables")
    op.drop_table("task_claims")
    op.drop_table("tasks")
    op.drop_table("agents")
    op.drop_table("categories")
    op.drop_table("users")

    for enum_name in [
        "review_key_source", "review_result", "llm_provider", "webhook_event",
        "transaction_type", "deliverable_status", "claim_status", "task_status",
        "agent_status", "user_role",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
