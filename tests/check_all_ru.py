import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def check_all_ru():
    api_key = os.getenv("ODDS_API_KEY")
    url = f"https://api.the-odds-api.com/v4/sports?apiKey={api_key}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                print("Found Russian/relevant leagues:")
                for sport in data:
                    lower_title = sport["title"].lower()
                    lower_key = sport["key"].lower()
                    if "russia" in lower_title or "russia" in lower_key or "vhl" in lower_key or "khl" in lower_key:
                        print(f" - {sport['key']} ({sport['title']})")
            else:
                print(f"Error {resp.status}")

if __name__ == "__main__":
    asyncio.run(check_all_ru())
