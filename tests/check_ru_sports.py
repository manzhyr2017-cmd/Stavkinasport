import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def check_ru_sports():
    api_key = os.getenv("ODDS_API_KEY")
    url = f"https://api.the-odds-api.com/v4/sports?apiKey={api_key}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                for sport in data:
                    if "russia" in sport["key"].lower():
                        print(f"RU Sport found: {sport['key']} ({sport['title']})")
            else:
                print(f"Error {resp.status}")

if __name__ == "__main__":
    asyncio.run(check_ru_sports())
