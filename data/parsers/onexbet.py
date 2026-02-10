import aiohttp
import logging
import json
from datetime import datetime
from typing import List, Dict, Optional
from data.parsers.base import BaseParser
from core.models import Match, BookmakerOdds, Market

logger = logging.getLogger(__name__)

class OneXBetParser(BaseParser):
    """
    Парсер 1xBet (используется как донор для многих РФ контор)
    """
    
    # Список актуальных зеркал
    MIRRORS = [
        "https://1xstavka.ru/LineFeed/GetLineZip?sports=1&count=200&lng=ru&mode=4",
        "https://melbet.ru/LineFeed/GetLineZip?sports=1&count=200&lng=ru&mode=4",
        "https://1xbet.com/LineFeed/GetLineZip?sports=1&count=200&lng=ru&mode=4"
    ]
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def bookmaker_name(self) -> str:
        return "1xBet (RU)"

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Connection": "keep-alive"
            })
        return self._session

    async def fetch_odds(self) -> List[Match]:
        session = await self._get_session()
        for url in self.MIRRORS:
            try:
                logger.debug(f"Trying 1xBet mirror: {url}")
                async with session.get(url, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        matches = self._parse_data(data)
                        if matches:
                            logger.info(f"1xBet: Successfully fetched {len(matches)} matches from {url}")
                            return matches
                    else:
                        text = await resp.text()
                        logger.warning(f"1xBet mirror {url} status: {resp.status}. Body: {text[:100]}...")
            except Exception as e:
                logger.debug(f"1xBet mirror {url} failed: {e}")
        
        logger.error("1xBet: All mirrors failed.")
        return []

    def _parse_data(self, data: Dict) -> List[Match]:
        matches = []
        events = data.get("Value", [])
        
        for event in events:
            try:
                # 1xBet структура: O1 (Win1), OX (Draw), O2 (Win2)
                # Находятся в списке 'E'
                odds = {}
                for bet in event.get("E", []):
                    t = bet.get("T")
                    v = bet.get("C")
                    if t == 1: odds["home"] = v
                    elif t == 2: odds["draw"] = v
                    elif t == 3: odds["away"] = v

                if not odds: continue

                m = Match(
                    id=f"1xbet_{event['I']}",
                    sport="soccer",
                    league=event.get("L", "Unknown League"),
                    home_team=event.get("O1", "Home"),
                    away_team=event.get("O2", "Away"),
                    commence_time=datetime.fromtimestamp(event["S"]),
                    bookmaker_odds=[BookmakerOdds(
                        bookmaker=self.bookmaker_name,
                        market=Market.H2H,
                        outcomes=odds
                    )]
                )
                matches.append(m)
            except Exception as e:
                continue

        logger.info(f"1xBet: Parsed {len(matches)} matches")
        return matches
