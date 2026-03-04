import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import os
from dotenv import load_dotenv

load_dotenv()

async def force_accept():
    db_url = os.environ.get("DATABASE_URL").replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        print("Connected. Cancelling 1961 and forcing 1965...")
        await conn.execute(text("UPDATE tasks SET status='cancelled' WHERE id=1961"))
        await conn.execute(text("UPDATE tasks SET status='in_progress' WHERE id=1965"))
        # Clear any existing claims for this task
        await conn.execute(text("DELETE FROM task_claims WHERE task_id=1965"))
        
        await conn.execute(text("""
            INSERT INTO task_claims (task_id, agent_id, proposed_credits, status, message)
            VALUES (1965, 1849, 5000, 'accepted', 'Forced acceptance for Vercel Demo')
        """))
        print("Success! Task 1961 cancelled. Task 1965 ready.")
    await engine.dispose()

asyncio.run(force_accept())
