"""Add 'evaluation' value to task_msg_type enum

Revision ID: 005
Revises: 004
Create Date: 2026-02-28
"""

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL requires ALTER TYPE outside a transaction for ADD VALUE.
    # Use IF NOT EXISTS so the migration is idempotent.
    op.execute("ALTER TYPE task_msg_type ADD VALUE IF NOT EXISTS 'evaluation'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op.
    pass
