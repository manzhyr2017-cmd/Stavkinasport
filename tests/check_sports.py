import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def get_sports():
    api_key = os.getenv("ODDS_API_KEY")
    url = f"https://api.the-odds-api.com/v4/sports?apiKey={api_key}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                soccer_keys = [s['key'] for s in data if 'soccer' in s['key']]
                print("Available soccer keys:")
                for key in sorted(soccer_keys):
                    print(f"  {key}")
            else:
                print(f"Error {resp.status}")

if __name__ == "__main__":
    asyncio.run(get_sports())
