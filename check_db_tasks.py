import asyncio
from sqlalchemy import select
from app.db.engine import engine
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Task

async def check_tasks():
    async with AsyncSession(engine) as session:
        result = await session.execute(
            select(Task).where(Task.status == "open").order_by(Task.id.desc())
        )
        tasks = result.scalars().all()
        print(f"Found {len(tasks)} open tasks:")
        for t in tasks:
            print(f"ID: {t.id}, Title: {t.title}, Status: {t.status}")

if __name__ == "__main__":
    asyncio.run(check_tasks())
