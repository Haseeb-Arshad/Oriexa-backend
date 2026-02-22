import asyncio
from sqlalchemy import select
from app.db.engine import engine
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Agent

async def check_agents():
    async with AsyncSession(engine) as session:
        result = await session.execute(
            select(Agent.id, Agent.name, Agent.api_key_hash, Agent.api_key_prefix).order_by(Agent.id.desc()).limit(5)
        )
        agents = result.all()
        for a in agents:
            print(f"ID: {a.id}, Name: {a.name}, Prefix: {a.api_key_prefix}, Hash: {a.api_key_hash}")

if __name__ == "__main__":
    asyncio.run(check_agents())
