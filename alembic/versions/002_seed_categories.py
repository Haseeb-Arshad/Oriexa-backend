"""Seed 7 default categories

Revision ID: 002
Revises: 001
Create Date: 2026-02-21
"""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

CATEGORIES = [
    ("Coding", "coding", "Software development tasks", "code", 1),
    ("Writing", "writing", "Content writing and copywriting", "pen", 2),
    ("Research", "research", "Research and analysis tasks", "search", 3),
    ("Data Processing", "data-processing", "Data entry and processing", "database", 4),
    ("Design", "design", "Graphic and UI design", "palette", 5),
    ("Translation", "translation", "Language translation tasks", "languages", 6),
    ("General", "general", "General purpose tasks", "briefcase", 7),
]


def upgrade() -> None:
    for name, slug, description, icon, sort_order in CATEGORIES:
        op.execute(
            f"INSERT INTO categories (name, slug, description, icon, sort_order) "
            f"VALUES ('{name}', '{slug}', '{description}', '{icon}', {sort_order}) "
            f"ON CONFLICT (name) DO NOTHING"
        )


def downgrade() -> None:
    for name, *_ in CATEGORIES:
        op.execute(f"DELETE FROM categories WHERE name = '{name}'")
