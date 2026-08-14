"""Add OpenCode Go LLM provider enum value.

Revision ID: 008
Revises: 007
Create Date: 2026-05-31
"""

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE llm_provider ADD VALUE IF NOT EXISTS 'opencode-go'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values without rebuilding the enum type.
    pass
