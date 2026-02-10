"""
=============================================================================
 BETTING ASSISTANT V2 — HISTORY IMPORTER
 Fetching past results from football-data.org and saving to DB
=============================================================================
"""
import logging
import asyncio
from typing import List
from datetime import datetime, timedelta
import aiohttp

from config.settings import api_config, betting_config
from data.database import AsyncSessionLocal, MatchHistory

logger = logging.getLogger(__name__)

class HistoryImporter:
    def __init__(self):
        self.api_key = api_config.FOOTBALL_DATA_KEY
        self.base_url = api_config.FOOTBALL_DATA_BASE

    async def fetch_league_results(self, league_code: str, days: int = 730):
        """Fetch results for a league for the last N days"""
        if not self.api_key:
            logger.error("FOOTBALL_DATA_KEY not set")
            return []

        # Convert simple league names to football-data codes (e.g., PL, BL1, PD)
        code_map = {
            "soccer_epl": "PL",
            "soccer_germany_bundesliga": "BL1",
            "soccer_spain_la_liga": "PD",
            "soccer_italy_serie_a": "SA",
            "soccer_france_ligue_one": "FL1",
            "soccer_portugal_primeira_liga": "PPL",
            "soccer_turkey_super_lig": "TR1",
            "soccer_brazil_campeonato": "BSA",
            "soccer_netherlands_eredivisie": "DED",
            "soccer_england_championship": "ELC"
        }
        api_code = code_map.get(league_code)
        if not api_code:
            logger.warning(f"No API code mapping for {league_code}")
            return []

        date_from = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = datetime.utcnow().strftime("%Y-%m-%d")
        
        url = f"{self.base_url}/competitions/{api_code}/matches"
        params = {"dateFrom": date_from, "dateTo": date_to, "status": "FINISHED"}
        headers = {"X-Auth-Token": self.api_key}

        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                logger.info(f"Fetching {league_code} history...")
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        await self._save_matches(data.get("matches", []), league_code)
                    elif resp.status == 429:
                        logger.warning("Rate limit hit, waiting 60s...")
                        await asyncio.sleep(60)
                        # Optional: retry once
                    elif resp.status == 403:
                        logger.warning(f"Free tier limit: cannot fetch 2 years for {league_code}. Trying 1 year...")
                        # Fallback to 365 days if 730 fails
                        params["dateFrom"] = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
                        async with session.get(url, params=params) as resp2:
                            if resp2.status == 200:
                                data = await resp2.json()
                                await self._save_matches(data.get("matches", []), league_code)
                            else:
                                logger.error(f"Fallback fetch failed with status {resp2.status}")
                    else:
                        logger.error(f"Error {resp.status} fetching history for {league_code}")
            except Exception as e:
                logger.error(f"Request error for {league_code}: {e}")

    async def _save_matches(self, matches: List[dict], league: str):
        async with AsyncSessionLocal() as session:
            for m in matches:
                history_entry = MatchHistory(
                    id=str(m["id"]),
                    sport=league,
                    league=m["competition"]["name"],
                    home_team=m["homeTeam"]["name"],
                    away_team=m["awayTeam"]["name"],
                    home_goals=m["score"]["fullTime"]["home"],
                    away_goals=m["score"]["fullTime"]["away"],
                    date=datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                )
                await session.merge(history_entry)
            await session.commit()
            logger.info(f"Saved {len(matches)} historical matches for {league}")

async def run_import():
    importer = HistoryImporter()
    for sport in betting_config.SPORTS:
        await importer.fetch_league_results(sport)
        await asyncio.sleep(2) # Rate limit

if __name__ == "__main__":
    asyncio.run(run_import())
