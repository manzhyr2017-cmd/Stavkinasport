import asyncio
import logging
import sys
import os

# Добавляем корневую директорию в путь
sys.path.append(os.getcwd())

from data.parsers.fonbet import FonbetParser
from data.parsers.onexbet import OneXBetParser

logging.basicConfig(level=logging.INFO)

# Для обхода Geo-блока (если есть переменная окружения)
PROXY = os.getenv("PROXY_URL") 
if PROXY:
    print(f"Using proxy: {PROXY}")

async def test_parsers():
    print("--- Testing Fonbet Parser ---")
    fb = FonbetParser()
    fb_matches = await fb.fetch_odds()
    print(f"Fonbet found {len(fb_matches)} matches")
    if fb_matches:
        m = fb_matches[0]
        print(f"First match: {m.home_team} vs {m.away_team} | League: {m.league}")
        print(f"Odds: {m.bookmaker_odds[0].outcomes if m.bookmaker_odds else 'No odds'}")
        
    await fb.close()

    
    # Пока нет cURL, отключаем 1xBet
    print("\n--- 1xBet Parser Disabled (waiting for cURL) ---")
    # ox = OneXBetParser()
    # ox_matches = await ox.fetch_odds()
    # print(f"1xBet found {len(ox_matches)} matches")
    # await ox.close()

if __name__ == "__main__":
    asyncio.run(test_parsers())
