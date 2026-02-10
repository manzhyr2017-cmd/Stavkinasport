import asyncio
import aiohttp
import logging

logging.basicConfig(level=logging.INFO)

ENDPOINTS = [
    "https://fon.bet/line/mobile/line/currentLine/ru",
    "https://www.fon.bet/line/mobile/line/currentLine/ru",
    "https://clientsapi.fonet-api.com/line/mobile/line/currentLine/ru",
    "https://1xbet.com/LineFeed/GetLineZip?sports=1&count=10&lng=ru&mode=4",
    "https://melbet.ru/LineFeed/GetLineZip?sports=1&count=10&lng=ru&mode=4",
    "https://line01.ccf4ab51771cacd46d.com/line/mobile/line/currentLine/ru"
]

async def discover():
    async with aiohttp.ClientSession(headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }) as session:
        for url in ENDPOINTS:
            try:
                print(f"Testing: {url}")
                async with session.get(url, timeout=5) as resp:
                    print(f"Status: {resp.status}")
                    if resp.status == 200:
                        print("SUCCESS!")
                        content = await resp.text()
                        print(f"Sample: {content[:100]}...")
            except Exception as e:
                print(f"FAILED: {e}")
            print("-" * 20)

if __name__ == "__main__":
    asyncio.run(discover())
