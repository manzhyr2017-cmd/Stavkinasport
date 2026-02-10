import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def list_bookmakers():
    api_key = os.getenv("ODDS_API_KEY")
    # Fetch EPL odds in EU region
    url = f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds?apiKey={api_key}&regions=eu&oddsFormat=decimal"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                if not data:
                    print("No events found")
                    return
                
                bms = set()
                for event in data:
                    for bm in event.get("bookmakers", []):
                        bms.add(bm["title"])
                
                print("Available Bookmakers (EU region):")
                for bm in sorted(list(bms)):
                    print(f" - {bm}")
            else:
                print(f"Error {resp.status}")

if __name__ == "__main__":
    asyncio.run(list_bookmakers())
