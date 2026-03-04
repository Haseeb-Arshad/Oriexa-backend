from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.engine import get_db
from app.db.models import Category

router = APIRouter()

@router.get("/categories")
async def get_categories(session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(Category).order_by(Category.sort_order)
    )
    return [
        {"id": c.id, "name": c.name, "slug": c.slug, "sort_order": c.sort_order}
        for c in result.scalars().all()
    ]
