"""
=============================================================================
 FONBET ENDPOINT HEALTH CHECKER + AUTO-DISCOVERY
 
 Проблема: Фонбет периодически меняет домены API линии.
 Решение: автоматический health-check всех known endpoints при старте,
          fallback на рабочий, кеширование рабочего endpoint.
 
 Актуальные данные на февраль 2026:
   - Официальный сайт: fon.bet (сменили с fonbet.ru)
   - Лицензия ФНС: Л027-00108-77/00395494
   - API линии: внутренние домены (line{N}.bk6.top и аналоги)
=============================================================================
"""
import asyncio
import gzip
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EndpointStatus:
    url: str
    alive: bool = False
    latency_ms: float = 0
    last_check: Optional[datetime] = None
    events_count: int = 0
    error: str = ""


class FonbetEndpointManager:
    """
    Управление endpoints Фонбет API.
    
    Логика:
    1. При старте проверяем все known endpoints
    2. Выбираем самый быстрый рабочий
    3. Каждые 10 минут перепроверяем
    4. Если текущий упал — автоматический fallback
    """

    # Все известные endpoints (обновлять при необходимости)
    KNOWN_ENDPOINTS = [
        "https://line-01.ccf4ab51771cacd46d.com",
        "https://line-02.ccf4ab51771cacd46d.com",
        "https://line-03.ccf4ab51771cacd46d.com",
        "https://line-01.cdnbk.net",
        "https://line-02.cdnbk.net",
        "https://line-01.fon.bet",
        "https://line-02.fon.bet",
        "https://line1.bk6.top",
        "https://line2.bk6.top",
        "https://line3.bk6.top",
        "https://line04.bk6.top",
        "https://line05.bk6.top",
        "https://line1.bk10.top",
        "https://line2.bk10.top",
    ]

    PREMATCH_PATH = "/line/currentLine/ru"
    LIVE_PATH = "/live/currentLine/ru"

    # User-Agent ротация (имитация браузеров)
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    ]

    def __init__(self):
        self._statuses: Dict[str, EndpointStatus] = {}
        self._active_endpoint: Optional[str] = None
        self._session = None
        self._last_full_check: Optional[datetime] = None
        self._recheck_interval = timedelta(minutes=10)

    async def _get_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Accept-Encoding": "gzip, deflate"},
            )
        return self._session

    def _random_ua(self) -> str:
        return random.choice(self.USER_AGENTS)

    async def check_endpoint(self, base_url: str) -> EndpointStatus:
        """Проверить один endpoint"""
        status = EndpointStatus(url=base_url)
        url = f"{base_url}{self.PREMATCH_PATH}?r={random.random()}"

        try:
            session = await self._get_session()
            start = time.monotonic()
            async with session.get(
                url, headers={"User-Agent": self._random_ua()}
            ) as resp:
                latency = (time.monotonic() - start) * 1000

                if resp.status != 200:
                    status.error = f"HTTP {resp.status}"
                    return status

                # Handle GZIP
                raw = await resp.read()
                try:
                    text = gzip.decompress(raw).decode("utf-8")
                except:
                    text = raw.decode("utf-8")

                data = json.loads(text)

                # Validate response structure
                if "events" not in data or "sports" not in data:
                    status.error = "Invalid JSON structure"
                    return status

                status.alive = True
                status.latency_ms = round(latency, 1)
                status.events_count = len(data.get("events", []))
                status.last_check = datetime.now()

        except asyncio.TimeoutError:
            status.error = "Timeout (10s)"
        except Exception as e:
            status.error = str(e)[:100]

        self._statuses[base_url] = status
        return status

    async def check_all(self) -> List[EndpointStatus]:
        """Проверить все known endpoints параллельно"""
        logger.info("🔍 Checking all Fonbet endpoints...")
        tasks = [self.check_endpoint(ep) for ep in self.KNOWN_ENDPOINTS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        statuses = []
        for r in results:
            if isinstance(r, EndpointStatus):
                statuses.append(r)

        # Выбрать лучший (alive + самый быстрый)
        alive = [s for s in statuses if s.alive]
        alive.sort(key=lambda s: s.latency_ms)

        if alive:
            best = alive[0]
            self._active_endpoint = best.url
            logger.info(
                f"✅ Best endpoint: {best.url} "
                f"({best.latency_ms:.0f}ms, {best.events_count} events)"
            )
        else:
            logger.error("❌ ALL Fonbet endpoints are DOWN!")
            self._active_endpoint = None

        self._last_full_check = datetime.now()

        # Log all results
        for s in statuses:
            icon = "✅" if s.alive else "❌"
            logger.info(
                f"  {icon} {s.url}: {s.latency_ms:.0f}ms, "
                f"{s.events_count} events | {s.error}"
            )

        return statuses

    async def get_active_endpoint(self) -> Optional[str]:
        """Получить рабочий endpoint (с автопроверкой)"""
        # Первый запуск
        if self._active_endpoint is None:
            await self.check_all()

        # Периодическая перепроверка
        if (self._last_full_check and
            datetime.now() - self._last_full_check > self._recheck_interval):
            await self.check_all()

        return self._active_endpoint

    async def fetch_with_fallback(self, live: bool = False) -> Optional[dict]:
        """
        Получить данные с автоматическим fallback.
        Если текущий endpoint упал — пробуем следующий.
        """
        path = self.LIVE_PATH if live else self.PREMATCH_PATH
        endpoint = await self.get_active_endpoint()

        if not endpoint:
            # Все мертвы — экстренная перепроверка
            await self.check_all()
            endpoint = self._active_endpoint
            if not endpoint:
                return None

        # Попытка с активным endpoint
        data = await self._fetch_one(endpoint, path)
        if data:
            return data

        # Fallback: пробуем все остальные
        logger.warning(f"⚠️ Active endpoint {endpoint} failed, trying fallbacks...")
        for ep_url in self.KNOWN_ENDPOINTS:
            if ep_url == endpoint:
                continue
            data = await self._fetch_one(ep_url, path)
            if data:
                self._active_endpoint = ep_url
                logger.info(f"🔄 Switched to fallback: {ep_url}")
                return data

        logger.error("❌ All endpoints failed!")
        return None

    async def _fetch_one(self, base_url: str, path: str) -> Optional[dict]:
        """Fetch от одного endpoint с GZIP + rate limit"""
        url = f"{base_url}{path}?r={random.random()}"
        try:
            # Random delay 1-3 sec (rate limiting protection)
            await asyncio.sleep(random.uniform(1.0, 3.0))

            session = await self._get_session()
            async with session.get(
                url, headers={"User-Agent": self._random_ua()}
            ) as resp:
                if resp.status != 200:
                    return None
                raw = await resp.read()
                try:
                    text = gzip.decompress(raw).decode("utf-8")
                except:
                    text = raw.decode("utf-8")
                data = json.loads(text)
                if "events" in data:
                    return data
        except Exception as e:
            logger.debug(f"Fetch failed {base_url}: {e}")
        return None

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
