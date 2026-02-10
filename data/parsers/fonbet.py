import aiohttp
import logging
from datetime import datetime
from typing import List, Dict, Optional
from data.parsers.base import BaseParser
from core.models import Match, BookmakerOdds, Market

logger = logging.getLogger(__name__)

class FonbetParser(BaseParser):
    """
    Парсер Fonbet через прямой API (динамические ресурсы).
    Использует актуальные эндпоинты из браузера.
    """
    
    # Список зеркал (ресурсов), полученных через F12
    MIRRORS = [
        "https://line-lb61-w.bk6bba-resources.com/ma/events/listBase?lang=ru&scopeMarket=1600",
        "https://line-01.bk6bba-resources.com/ma/events/listBase?lang=ru&scopeMarket=1600",
        "https://www.fon.bet/line/mobile/line/currentLine/ru"
    ]
    
    # Факторы исходов (П1, Х, П2, ТМ, ТБ)
    # Поддерживаем и старые (1,2,3) и новые (921,922,923) ID
    FACTORS = {
        1: "home", 2: "draw", 3: "away",
        921: "home", 922: "draw", 923: "away",
        9: "under", 10: "over",
        17: "over", 18: "under"
    }

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def bookmaker_name(self) -> str:
        return "Fonbet"

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "Accept": "*/*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Origin": "https://fon.bet",
                "Referer": "https://fon.bet/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site"
            })
        return self._session

    async def fetch_odds(self) -> List[Match]:
        session = await self._get_session()
        
        for url in self.MIRRORS:
            try:
                async with session.get(url, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        matches = self._parse_data(data)
                        if matches:
                            logger.info(f"Fonbet: Successfully fetched {len(matches)} matches from {url}")
                            return matches
                    else:
                        logger.warning(f"Mirror {url} returned status {resp.status}")
            except Exception as e:
                logger.debug(f"Mirror {url} failed: {e}")
        
        logger.error("Fonbet: All mirrors failed or returned no data.")
        return []

    def _parse_data(self, data: Dict) -> List[Match]:
        # 1. Маппинг турниров и спортов
        tournaments = {}
        for t in data.get("tournamentInfos", []):
            if "id" in t and "name" in t:
                tournaments[str(t["id"])] = t["name"]
        
        sports_map = {}
        for s in data.get("sports", []):
            sports_map[str(s["id"])] = s.get("name", "")

        # 2. Поиск всех футбольных ID
        sports = data.get("sports", [])
        soccer_ids = set()
        
        # Ищем разделы "Футбол"
        for sport in sports:
            name = sport.get("name", "").lower()
            sid = sport["id"]
            # 11918 - это точно футбол (из прошлых логов)
            if sid == 11918 or ("футбол" in name and "кибер" not in name and "статистика" not in name):
                soccer_ids.add(sid)
        
        # Добавляем детей (лиги)
        for _ in range(3):
            added = False
            for sport in sports:
                sid = sport["id"]
                pid = sport.get("parentId")
                if pid in soccer_ids and sid not in soccer_ids:
                    soccer_ids.add(sid)
                    added = True
            if not added: break
        
        # 3. Парсим события
        events = data.get("events", [])
        events_map = {}
        for event in events:
            sid_raw = event.get("sportId")
            
            # Проверяем наличие команд и принадлежность к футболу
            if sid_raw in soccer_ids and event.get("team1") and event.get("team2"):
                tid = str(event.get("tournamentId"))
                
                # Приоритет 1: Ищем в tournamentInfos
                league_name = tournaments.get(tid)
                
                # Приоритет 2: Ищем в sports (если tournamentId совпадает с ID спорта/лиги)
                if not league_name:
                    league_name = sports_map.get(tid)
                    
                # Приоритет 3: Берем имя родительского раздела (sportId)
                if not league_name:
                    league_name = sports_map.get(str(sid_raw))

                events_map[event["id"]] = {
                    "home": event["team1"],
                    "away": event["team2"],
                    "time": datetime.fromtimestamp(event.get("startTime", 0)),
                    "league": league_name or "Unknown League",
                    "odds": {}
                }
        
        return self._parse_odds(data, events_map)

    def _parse_odds(self, data: Dict, events_map: Dict) -> List[Match]:
        # Вынес парсинг кэфов в отдельный метод для ясности

        # Парсим котировки
        factors_data = data.get("customFactors", [])
        
        for event_block in factors_data:
            event_id = event_block.get("e")
            if event_id not in events_map:
                continue
            
            # В listBase факторы лежат в списке 'factors' внутри блока события
            for factor in event_block.get("factors", []):
                factor_id = factor.get("f")
                value = factor.get("v")
                
                if factor_id in self.FACTORS:
                    outcome = self.FACTORS[factor_id]
                    events_map[event_id]["odds"][outcome] = value

        # Собираем объекты Match
        matches = []
        for eid, info in events_map.items():
            if not info["odds"]: continue
            
            # Разделяем на маркеты (упрощенно)
            h2h_odds = {k: v for k, v in info["odds"].items() if k in ["home", "draw", "away"]}
            
            bo = []
            if h2h_odds:
                bo.append(BookmakerOdds(
                    bookmaker=self.bookmaker_name,
                    market=Market.H2H,
                    outcomes=h2h_odds
                ))

            matches.append(Match(
                id=f"fonbet_{eid}",
                sport="soccer", # Упрощенно
                league=info["league"],
                home_team=info["home"],
                away_team=info["away"],
                commence_time=info["time"],
                bookmaker_odds=bo
            ))
            
        logger.info(f"Fonbet: Parsed {len(matches)} matches")
        return matches
