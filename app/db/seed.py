"""Seed the categories table with the 7 default categories."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CATEGORIES = [
    {"name": "Coding", "slug": "coding", "description": "Software development tasks", "icon": "code", "sort_order": 1},
    {"name": "Writing", "slug": "writing", "description": "Content writing and copywriting", "icon": "pen", "sort_order": 2},
    {"name": "Research", "slug": "research", "description": "Research and analysis tasks", "icon": "search", "sort_order": 3},
    {"name": "Data Processing", "slug": "data-processing", "description": "Data entry and processing", "icon": "database", "sort_order": 4},
    {"name": "Design", "slug": "design", "description": "Graphic and UI design", "icon": "palette", "sort_order": 5},
    {"name": "Translation", "slug": "translation", "description": "Language translation tasks", "icon": "languages", "sort_order": 6},
    {"name": "General", "slug": "general", "description": "General purpose tasks", "icon": "briefcase", "sort_order": 7},
]


async def seed_categories(session: AsyncSession) -> None:
    for cat in CATEGORIES:
        await session.execute(
            text(
                """
                INSERT INTO categories (name, slug, description, icon, sort_order)
                VALUES (:name, :slug, :description, :icon, :sort_order)
                ON CONFLICT (name) DO NOTHING
                """
            ),
            cat,
        )
    await session.commit()
