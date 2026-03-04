"""Add task_messages table for conversation timeline

Revision ID: 004
Revises: 003
Create Date: 2026-02-24
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Skip if table already exists (idempotent)
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'task_messages')")
    )
    if result.scalar():
        return

    # Create enum types (idempotent via DO $$ EXCEPTION handler)
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE task_msg_sender_type AS ENUM ('poster','agent','system'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE task_msg_type AS ENUM "
        "('text','question','attachment','claim_proposal',"
        "'status_change','revision_request','remark'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )

    # Use raw SQL to avoid SQLAlchemy's DDL visitor trying to CREATE TYPE again
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
    op.execute("DROP TABLE IF EXISTS task_messages")
    op.execute("DROP TYPE IF EXISTS task_msg_type")
    op.execute("DROP TYPE IF EXISTS task_msg_sender_type")
