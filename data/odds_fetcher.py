"""
=============================================================================
 BETTING ASSISTANT — DATA ACQUISITION LAYER
 Получение live-коэффициентов через The Odds API
 + кеширование в Redis + сохранение истории в PostgreSQL
=============================================================================
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp

from config.settings import api_config, betting_config
from core.models import BookmakerOdds, Market, Match
from data.mappings import translate_team_name
from data.parsers.fonbet import FonbetParser
from data.parsers.onexbet import OneXBetParser

logger = logging.getLogger(__name__)


class OddsDataFetcher:
    """
    Улучшенный модуль получения коэффициентов.
    Сочетает The Odds API (для Pinnacle/Sharp) и прямые парсеры (для РФ рынка).
    """

    def __init__(self, redis_client=None):
        self.api_key = api_config.ODDS_API_KEY
        self.base_url = api_config.ODDS_API_BASE
        self.redis = redis_client
        self._session: Optional[aiohttp.ClientSession] = None
        self._requests_used = 0
        self._requests_remaining = 500
        
        # РФ Парсеры
        self.ru_parsers = [
            FonbetParser(),
            OneXBetParser()
        ]

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        
        # Закрываем сессии парсеров
        for parser in self.ru_parsers:
            if hasattr(parser, 'close'):
                await parser.close()

    # ----- Основные методы -----

    async def fetch_sports(self) -> List[dict]:
        """Получить список доступных спортов/лиг"""
        url = f"{self.base_url}/sports"
        params = {"apiKey": self.api_key}
        return await self._request(url, params)

    async def fetch_odds(
        self,
        sport: str,
        markets: str = "h2h",
        regions: str = "eu",
    ) -> List[Match]:
        """
        Получить текущие коэффициенты для спорта.
        
        Args:
            sport: Ключ спорта (например 'soccer_epl')
            markets: Рынки через запятую ('h2h,totals,spreads')
            regions: Регион букмекеров ('eu', 'us', 'uk')
        
        Returns:
            Список Match-объектов с коэффициентами
        """
        url = f"{self.base_url}/sports/{sport}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
        }

        raw_data = await self._request(url, params)
        if not raw_data:
            return []

        matches = []
        for event in raw_data:
            match = self._parse_event(event, sport)
            if match:
                matches.append(match)
                # Кешируем в Redis (TTL = 5 минут)
                if self.redis:
                    await self._cache_match(match)

        logger.info(f"Fetched {len(matches)} events for {sport}")
        return matches

    async def fetch_all_sports_odds(self) -> Dict[str, List[Match]]:
        """
        Оптимизированное получение кф с кешированием в Redis.
        1. Проверяет кеш в Redis.
        2. Если пусто — делает один запрос к API за всеми данными.
        3. Сохраняет результат в кеш на время ODDS_POLL_INTERVAL.
        """
        all_matches = {sport: [] for sport in betting_config.SPORTS}
        
        # 1. Пробуем получить из кеша
        if self.redis:
            cached = await self.redis.get("odds:all_soccer")
            if cached:
                logger.info("📦 Using cached odds from Redis (Pre-match optimization)")
                data = json.loads(cached)
                # Парсим закешированные события
                count = 0
                for event in data:
                    sport_key = event.get("sport_key")
                    if sport_key in betting_config.SPORTS:
                        match = self._parse_event(event, sport_key)
                        if match:
                            all_matches[sport_key].append(match)
                            count += 1
                
                # Даже если есть кеш, добавляем Live-данные от парсеров
                await self._integrate_ru_parsers(all_matches)
                return all_matches

        # 2. Если в кеше нет, идем в API
        markets_str = ",".join(betting_config.MARKETS)
        url = f"{self.base_url}/sports/soccer/odds"
        params = {
            "apiKey": self.api_key,
            "regions": betting_config.BOOKMAKER_REGIONS,
            "markets": markets_str,
            "oddsFormat": "decimal",
        }

        logger.info("📡 Fetching FRESH odds from API...")
        raw_data = await self._request(url, params)
        
        if not raw_data:
            # Если API не ответил, всё равно пробуем парсеры
            await self._integrate_ru_parsers(all_matches)
            return all_matches

        # 3. Сохраняем в кеш (на 15-30 минут согласно конфигу)
        if self.redis and raw_data:
            await self.redis.setex(
                "odds:all_soccer", 
                betting_config.ODDS_POLL_INTERVAL, 
                json.dumps(raw_data)
            )
            logger.info(f"💾 Fresh odds cached for {betting_config.ODDS_POLL_INTERVAL}s")

        count = 0
        for event in raw_data:
            sport_key = event.get("sport_key")
            if sport_key in betting_config.SPORTS:
                match = self._parse_event(event, sport_key)
                if match:
                    all_matches[sport_key].append(match)
                    count += 1

        logger.info(f"Optimization: Parsed {count} matches from fresh API response.")
        
        # 4. Добавляем данные из прямых РФ парсеров
        await self._integrate_ru_parsers(all_matches)
        
        return all_matches

    async def _integrate_ru_parsers(self, all_matches: Dict[str, List[Match]]):
        """Интеграция данных от парсеров (Fonbet, 1xBet) с переводом названий"""
        for parser in self.ru_parsers:
            try:
                # Временно пропускаем 1xBet, если он падает (пока не починим cURL)
                if "1xBet" in parser.bookmaker_name:
                     # Можно раскомментировать, когда починим
                     # continue 
                     pass

                ru_matches = await parser.fetch_odds()
                count = 0
                for rm in ru_matches:
                    # 1. Переводим названия команд
                    rm.home_team = translate_team_name(rm.home_team)
                    rm.away_team = translate_team_name(rm.away_team)
                    
                    # 2. Пересобираем ID, чтобы он соответствовал английским названиям
                    # Это важно для дедупликации и поиска
                    
                    # Группируем по источнику
                    # Например: soccer_ru_fonbet
                    target_key = f"soccer_ru_{parser.bookmaker_name.lower().split()[0]}"
                    
                    if target_key not in all_matches:
                        all_matches[target_key] = []
                    
                    all_matches[target_key].append(rm)
                    count += 1
                
                if count > 0:
                    logger.info(f"✅ Integrated {count} matches from {parser.bookmaker_name} (translated)")
            except Exception as e:
                logger.error(f"Error in {parser.bookmaker_name} parser: {e}")

    async def fetch_historical_odds(
        self, sport: str, event_id: str, date: str
    ) -> dict:
        """Исторические кф для бэктестинга (платная фича API)"""
        url = f"{self.base_url}/historical/sports/{sport}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "eu",
            "markets": "h2h",
            "date": date,  # ISO формат
        }
        return await self._request(url, params)

    # ----- Парсинг данных -----

    def _parse_event(self, event: dict, sport: str) -> Optional[Match]:
        """Парсинг одного события из API"""
        try:
            bookmaker_odds = []
            for bm in event.get("bookmakers", []):
                for market_data in bm.get("markets", []):
                    market_key = market_data.get("key", "h2h")
                    outcomes = {}
                    for outcome in market_data.get("outcomes", []):
                        name = outcome["name"].lower()
                        # Нормализуем имена исходов
                        if name == event.get("home_team", "").lower():
                            outcomes["home"] = outcome["price"]
                        elif name == event.get("away_team", "").lower():
                            outcomes["away"] = outcome["price"]
                        elif name == "draw":
                            outcomes["draw"] = outcome["price"]
                        elif name == "over":
                            outcomes["over"] = outcome["price"]
                        elif name == "under":
                            outcomes["under"] = outcome["price"]
                        else:
                            outcomes[name] = outcome["price"]

                    bookmaker_odds.append(BookmakerOdds(
                        bookmaker=bm["title"],
                        market=Market(market_key) if market_key in Market.__members__.values() else Market.H2H,
                        outcomes=outcomes,
                        last_update=datetime.fromisoformat(
                            bm.get("last_update", datetime.utcnow().isoformat()).replace("Z", "+00:00")
                        ),
                    ))

            return Match(
                id=event["id"],
                sport=sport,
                league=sport.replace("soccer_", "").replace("_", " ").title(),
                home_team=event["home_team"],
                away_team=event["away_team"],
                commence_time=datetime.fromisoformat(
                    event["commence_time"].replace("Z", "+00:00")
                ),
                bookmaker_odds=bookmaker_odds,
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to parse event: {e}")
            return None

    # ----- Кеширование -----

    async def _cache_match(self, match: Match):
        """Кешируем данные матча в Redis"""
        if not self.redis:
            return
        key = f"match:{match.id}"
        data = {
            "id": match.id,
            "sport": match.sport,
            "home": match.home_team,
            "away": match.away_team,
            "best_odds": match.best_odds,
            "avg_odds": match.avg_odds,
            "updated": datetime.utcnow().isoformat(),
        }
        await self.redis.setex(key, 300, json.dumps(data))  # TTL 5 мин

    # ----- HTTP -----

    async def _request(self, url: str, params: dict) -> list | dict:
        session = await self._get_session()
        try:
            async with session.get(url, params=params, timeout=30) as resp:
                # Отслеживаем лимит запросов
                self._requests_used = int(
                    resp.headers.get("x-requests-used", 0)
                )
                self._requests_remaining = int(
                    resp.headers.get("x-requests-remaining", 500)
                )

                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    logger.error("❌ Invalid Odds API key! Disabling further API requests.")
                    self.api_key = None # Prevent further requests
                elif resp.status == 429:
                    logger.warning("⚠️ Odds API Rate limit exceeded!")
                    self._requests_remaining = 0
                else:
                    text = await resp.text()
                    logger.error(f"API error {resp.status}: {text}")
                return []
        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching {url}")
            return []
        except Exception as e:
            logger.error(f"Request error: {e}")
            return []

    @property
    def api_usage_info(self) -> str:
        return (
            f"API Usage: {self._requests_used} used, "
            f"{self._requests_remaining} remaining"
        )
