import asyncio
from data.database import AsyncSessionLocal, MatchHistory
from sqlalchemy import select, func

async def count():
    async with AsyncSessionLocal() as session:
        n = await session.scalar(select(func.count(MatchHistory.id)))
        print(f"Match count: {n}")

if __name__ == "__main__":
    asyncio.run(count())
