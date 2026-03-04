import asyncio
import json
from sqlalchemy import select
from app.db.engine import engine
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Task

async def check_remarks(task_id):
    async with AsyncSession(engine) as session:
        result = await session.execute(
            select(Task).where(Task.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task:
            print(f"Task ID: {task.id}")
            print(f"Title: {task.title}")
            print(f"Remarks: {json.dumps(task.agent_remarks, indent=2)}")
        else:
            print(f"Task {task_id} not found")

if __name__ == "__main__":
    import sys
    tid = int(sys.argv[1]) if len(sys.argv) > 1 else 1878
    asyncio.run(check_remarks(tid))
