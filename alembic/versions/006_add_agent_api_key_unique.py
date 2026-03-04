"""Add unique index on agents.api_key_hash

Revision ID: 006
Revises: 005
Create Date: 2026-03-04
"""

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create unique index idempotently — safe to re-run
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS agents_api_key_hash_unique
        ON agents (api_key_hash)
        WHERE api_key_hash IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS agents_api_key_hash_unique")
