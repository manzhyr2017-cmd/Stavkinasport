import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from dotenv import load_dotenv

load_dotenv()

async def test_conn():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("⏭️ Skipping DB test: DATABASE_URL not set.")
        return
    
    # Mask password for logging
    target = db_url.split('@')[1] if '@' in db_url else db_url
    print(f"Testing connection to: {target}")
    
    try:
        engine = create_async_engine(db_url, connect_args={"ssl": False})
        async with engine.connect() as conn:
            await conn.execute("SELECT 1")
            print("✅ CONNECTION SUCCESSFUL!")
        await engine.dispose()
    except Exception as e:
        print(f"❌ CONNECTION FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_conn())
