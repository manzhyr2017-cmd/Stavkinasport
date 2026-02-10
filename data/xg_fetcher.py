"""
=============================================================================
 BETTING ASSISTANT V2 — xG DATA FETCHER
 Fetching Expected Goals from API-Football or mocking for development
=============================================================================
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime
import aiohttp

from config.settings import api_config

logger = logging.getLogger(__name__)

class XGFetcher:
    """
    Fetches xG (Expected Goals) and advanced stats.
    Primary source: API-Football (RapidAPI)
    """

    def __init__(self):
        self.api_key = api_config.RAPID_API_KEY
        self.host = api_config.API_FOOTBALL_HOST
        self.base_url = f"https://{self.host}/v3"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "x-rapidapi-key": self.api_key,
                "x-rapidapi-host": self.host
            })
        return self._session

    async def fetch_match_xg(self, fixture_id: int) -> Optional[dict]:
        """Fetch xG for a specific match"""
        if not self.api_key:
            logger.warning("RAPID_API_KEY not set. Cannot fetch xG.")
            return None

        url = f"{self.base_url}/fixtures/statistics"
        params = {"fixture": fixture_id}
        
        session = await self._get_session()
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return self._parse_xg(data)
                return None
        except Exception as e:
            logger.error(f"Error fetching xG: {e}")
            return None

    def _parse_xg(self, data: dict) -> dict:
        """Parse RapidAPI stats into home/away xG"""
        stats = data.get("response", [])
        result = {"home_xg": 0.0, "away_xg": 0.0}
        
        for team_stats in stats:
            team_side = "home" if team_stats.get("team", {}).get("name") else "away" # Simplified
            for stat in team_stats.get("statistics", []):
                if stat.get("type") == "expected_goals":
                    val = stat.get("value")
                    if val:
                        result[f"{team_side}_xg"] = float(val)
        return result

    async def close(self):
        if self._session:
            await self._session.close()
