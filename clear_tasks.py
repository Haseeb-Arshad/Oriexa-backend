import asyncio
from sqlalchemy import delete
from app.db.engine import engine
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Task, TaskClaim, Deliverable, Review, CreditTransaction, SubmissionAttempt

async def clear_all_tasks():
    async with AsyncSession(engine) as session:
        async with session.begin():
            print("Deleting submission attempts...")
            await session.execute(delete(SubmissionAttempt))
            
            print("Deleting reviews...")
            await session.execute(delete(Review))
            
            print("Deleting deliverables...")
            await session.execute(delete(Deliverable))
            
            print("Deleting task claims...")
            await session.execute(delete(TaskClaim))
            
            print("Deleting credit transactions for tasks...")
            # We only delete transactions linked to tasks, keep the rest (bonuses, etc.)
            await session.execute(delete(CreditTransaction).where(CreditTransaction.task_id.isnot(None)))
            
            print("Deleting tasks...")
            await session.execute(delete(Task))
            
            await session.commit()
            print("✅ All tasks and related data have been cleared from the database.")

if __name__ == "__main__":
    asyncio.run(clear_all_tasks())
