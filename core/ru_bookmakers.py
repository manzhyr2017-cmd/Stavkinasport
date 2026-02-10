"""
=============================================================================
 RUSSIAN BOOKMAKERS ADAPTER — FONBET + 1XBET + MELBET

 Полная адаптация под русские БК с упором на:
   1. Парсинг линии Фонбет (line API + live API)
   2. Мультиспорт (футбол, хоккей, баскетбол, теннис, киберспорт)
   3. Оптимизация экспрессов под бонусы Фонбет
   4. Страховка экспресса (6+ событий, кф ≥ 1.60)

 Источники данных:
   - Фонбет: line{N}.bk6.top/live/currentLine, /line/currentLine
   - 1xBet: 1xstavka.ru API (аналогичная структура)
   - ODDSCORP API (платный, api.oddscp.com) — агрегатор
   - OddsAPI.ru — альтернативный агрегатор



 Маржа Фонбет по видам спорта (данные 2025):
   - Футбол (АПЛ, Бундеслига): 4-5%
   - Футбол (Серия А, Ла Лига): 5-5.5%
   - Хоккей (КХЛ, НХЛ): 5-6%
   - Баскетбол (НБА, Евролига): 5-6%
   - Теннис (ТБШ, ATP/WTA): 5-6%
   - Киберспорт (CS2, Dota 2): 5-7%
   - Лайв: 7-8.5%
   - Низшие лиги: 7-8.5%
=============================================================================
"""
import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


from core.fonbet_health import FonbetEndpointManager

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  ENUMS & MODELS
# ═══════════════════════════════════════════════════════════

class Bookmaker(Enum):
    FONBET = "fonbet"
    XSTAVKA = "1xstavka"   # 1xBet RU (легальная)
    MELBET = "melbet"
    WINLINE = "winline"
    BETCITY = "betcity"
    LEON = "leon"
    LIGASTAVOK = "ligastavok"
    OLIMP = "olimp"
    BETBOOM = "betboom"
    PARI = "pari"


class Sport(Enum):
    FOOTBALL = "football"
    HOCKEY = "hockey"
    BASKETBALL = "basketball"
    TENNIS = "tennis"
    VOLLEYBALL = "volleyball"
    ESPORTS = "esports"
    HANDBALL = "handball"
    MMA = "mma"
    TABLE_TENNIS = "table_tennis"


class Market(Enum):
    """Рынки ставок в русских БК"""
    # Исходы
    WIN1 = "П1"
    DRAW = "Х"
    WIN2 = "П2"
    WIN1_OR_DRAW = "1Х"
    DRAW_OR_WIN2 = "Х2"
    WIN1_OR_WIN2 = "12"

    # Тоталы
    TOTAL_OVER = "ТБ"
    TOTAL_UNDER = "ТМ"
    INDIVIDUAL_TOTAL_1_OVER = "ИТ1Б"
    INDIVIDUAL_TOTAL_1_UNDER = "ИТ1М"
    INDIVIDUAL_TOTAL_2_OVER = "ИТ2Б"
    INDIVIDUAL_TOTAL_2_UNDER = "ИТ2М"

    # Форы
    HANDICAP_1 = "Ф1"
    HANDICAP_2 = "Ф2"

    # Обе забьют
    BOTH_SCORE_YES = "ОЗ_Да"
    BOTH_SCORE_NO = "ОЗ_Нет"


@dataclass
class RuMatch:
    """Матч в формате русских БК"""
    id: str
    sport: Sport
    league: str
    home_team: str
    away_team: str
    start_time: datetime
    is_live: bool = False

    # Коэффициенты по рынкам
    odds: Dict[str, float] = field(default_factory=dict)

    # Мета
    bookmaker: Bookmaker = Bookmaker.FONBET
    fonbet_event_id: int = 0
    sport_id: int = 0
    score: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.home_team} — {self.away_team}"

    @property
    def overround(self) -> float:
        """Маржа по основным исходам (1X2 или 12)"""
        if self.sport in (Sport.TENNIS, Sport.ESPORTS,
                          Sport.TABLE_TENNIS, Sport.VOLLEYBALL):
            # 2-way
            p1 = self.odds.get("П1", 0)
            p2 = self.odds.get("П2", 0)
            if p1 > 0 and p2 > 0:
                return (1/p1 + 1/p2) - 1
        else:
            # 3-way
            p1 = self.odds.get("П1", 0)
            px = self.odds.get("Х", 0)
            p2 = self.odds.get("П2", 0)
            if p1 > 0 and p2 > 0:
                total = 1/p1 + 1/p2
                if px > 0:
                    total += 1/px
                return total - 1
        return 0

    def to_telegram(self) -> str:
        sport_emoji = {
            Sport.FOOTBALL: "⚽", Sport.HOCKEY: "🏒",
            Sport.BASKETBALL: "🏀", Sport.TENNIS: "🎾",
            Sport.VOLLEYBALL: "🏐", Sport.ESPORTS: "🎮",
            Sport.HANDBALL: "🤾", Sport.MMA: "🥊",
            Sport.TABLE_TENNIS: "🏓",
        }
        emoji = sport_emoji.get(self.sport, "🏅")
        live = "🔴 LIVE" if self.is_live else ""
        time_str = self.start_time.strftime("%H:%M")

        lines = [
            f"{emoji} {self.display_name} {live}",
            f"📅 {time_str} | {self.league}",
        ]
        # Основные кф
        if "П1" in self.odds:
            line = f"П1: {self.odds['П1']:.2f}"
            if "Х" in self.odds:
                line += f" | Х: {self.odds['Х']:.2f}"
            line += f" | П2: {self.odds['П2']:.2f}"
            lines.append(line)

        return "\n".join(lines)


@dataclass
class RuExpressBet:
    """Экспресс оптимизированный под Фонбет"""
    legs: List[dict]  # [{"match": RuMatch, "market": str, "odds": float}, ...]
    total_odds: float = 0
    probability: float = 0
    ev: float = 0
    stake: float = 0
    potential_win: float = 0
    correlation_discount: float = 1.0

    # Бонусы Фонбет
    insurance_eligible: bool = False   # Страховка экспресса (6+, кф≥1.60)
    bonus_multiplier: float = 1.0      # Повышенный кф за N событий

    @property
    def num_legs(self) -> int:
        return len(self.legs)

    @property
    def effective_ev(self) -> float:
        """EV с учётом страховки"""
        if self.insurance_eligible:
            # Страховка = возврат ставки если 1 нога не прошла
            # P(exactly 1 loss) = sum(P(leg_i_loss) * prod(P(other_wins)))
            probs = [leg.get("prob", 0.5) for leg in self.legs]
            p_one_loss = 0
            for i in range(len(probs)):
                p_loss = 1 - probs[i]
                p_rest_win = 1
                for j, p in enumerate(probs):
                    if j != i:
                        p_rest_win *= p
                p_one_loss += p_loss * p_rest_win

            # С страховкой: EV = P(all_win)*profit + P(1_loss)*0 - P(2+_loss)*stake
            p_all_win = self.probability
            p_two_plus_loss = 1 - p_all_win - p_one_loss
            profit = self.stake * (self.total_odds - 1)
            return (p_all_win * profit - p_two_plus_loss * self.stake) / self.stake
        return self.ev

    def to_telegram(self) -> str:
        lines = [f"🔥 ЭКСПРЕСС ({self.num_legs} событий)"]
        if self.insurance_eligible:
            lines[0] += " 🛡️ СТРАХОВКА"

        for i, leg in enumerate(self.legs, 1):
            m = leg.get("match")
            market = leg.get("market", "")
            odds = leg.get("odds", 0)
            if m:
                sport_emoji = {"football": "⚽", "hockey": "🏒",
                               "basketball": "🏀", "tennis": "🎾",
                               "esports": "🎮"}.get(m.sport.value, "🏅")
                lines.append(
                    f"  {i}. {sport_emoji} {m.display_name}\n"
                    f"     {market} @ {odds:.2f} ({m.league})"
                )

        lines.append(f"\n📊 Общий кф: {self.total_odds:.2f}")
        lines.append(f"📈 EV: {self.ev:+.1%}")
        if self.insurance_eligible:
            lines.append(f"🛡️ EV со страховкой: {self.effective_ev:+.1%}")
        if self.correlation_discount < 1:
            lines.append(f"⚠️ Корреляция: {self.correlation_discount:.0%}")
        lines.append(f"💰 Ставка: {self.stake:.0f}₽ → {self.potential_win:.0f}₽")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  FONBET PARSER
# ═══════════════════════════════════════════════════════════

class FonbetParser:
    """
    Парсер линии Фонбет через их внутренний API.
    
    Архитектура Фонбет API:
    - line{N}.bk6.top/line/currentLine/ru — прематч линия
    - line{N}.bk6.top/live/currentLine/ru — лайв линия
    
    JSON структура:
    {
        "sports": [{"id": 1, "name": "Футбол"}, ...],
        "events": [{"id": 123, "sportId": 1, "team1": "...", 
                    "team2": "...", "startTime": 1234567890, ...}],
        "eventMiscs": [{"id": 123, "score1": 0, "score2": 1}],
        "eventBlocks": [{"id": 123, "state": "open/blocked/partial"}],
        "customFactors": [{"e": 123, "f": 921, "v": 1.85}]
    }
    
    Factor IDs (типы ставок):
        921  = П1 (win1)
        922  = Х (draw)  
        923  = П2 (win2)
        1571 = 12 (win1 or win2, для двухисходных)
        924  = 1Х
        925  = Х2
        930  = ТБ (total over) — параметр в p1
        931  = ТМ (total under) — параметр в p2
        927  = Ф1 (handicap 1) — параметр в p1
        928  = Ф2 (handicap 2) — параметр в p2
        1845 = ОЗ Да (both to score yes)
        1846 = ОЗ Нет (both to score no)
    """

    FACTOR_MAP = {
        921: "П1", 922: "Х", 923: "П2",
        924: "1Х", 925: "Х2", 1571: "12",
        1845: "ОЗ_Да", 1846: "ОЗ_Нет",
    }

    SPORT_MAP = {
        1: Sport.FOOTBALL, 2: Sport.HOCKEY,
        3: Sport.BASKETBALL, 4: Sport.TENNIS,
        6: Sport.VOLLEYBALL, 12: Sport.HANDBALL,
        40: Sport.ESPORTS, 45: Sport.TABLE_TENNIS,
        9: Sport.MMA,
    }

    # Топ-лиги с минимальной маржей (4-6%)
    TOP_LEAGUES = {
        Sport.FOOTBALL: [
            "Англия. Премьер-лига", "Германия. Бундеслига",
            "Испания. Ла Лига", "Италия. Серия А",
            "Франция. Лига 1", "Лига чемпионов. Плей-офф",
            "Лига Европы", "Россия. Премьер-лига",
        ],
        Sport.HOCKEY: [
            "КХЛ", "НХЛ", "ВХЛ",
            "Чемпионат мира",
        ],
        Sport.BASKETBALL: [
            "НБА", "Евролига", "Единая лига ВТБ",
        ],
        Sport.TENNIS: [
            "ATP", "WTA", "Большой шлем",
            "Australian Open", "Roland Garros",
            "Wimbledon", "US Open",
        ],
        Sport.ESPORTS: [
            "CS2", "Dota 2", "League of Legends",
            "Valorant",
        ],
    }

    # Средняя маржа по спорту (для Shin's removal)
    MARGIN_BY_SPORT = {
        Sport.FOOTBALL: 0.045,
        Sport.HOCKEY: 0.055,
        Sport.BASKETBALL: 0.055,
        Sport.TENNIS: 0.055,
        Sport.VOLLEYBALL: 0.06,
        Sport.ESPORTS: 0.06,
        Sport.HANDBALL: 0.065,
        Sport.TABLE_TENNIS: 0.07,
        Sport.MMA: 0.06,
    }

    def __init__(self):
        self.endpoint_manager = FonbetEndpointManager()
        self._session = None

    async def _get_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def fetch_line(self, live: bool = False) -> List[RuMatch]:
        """Получить линию (прематч или лайв) с авто-восстановлением"""
        data = await self.endpoint_manager.fetch_with_fallback(live=live)
        if not data:
            return []

        # Offload parsing to thread (CPU bound)
        return await asyncio.to_thread(self._parse_response, data, is_live=live)

    def _parse_response(self, data: dict, is_live: bool = False) -> List[RuMatch]:
        """Парсинг JSON ответа Фонбет"""
        sports = {s["id"]: s.get("name", "") for s in data.get("sports", [])}
        events_raw = data.get("events", [])
        factors_raw = data.get("customFactors", [])
        blocks_raw = data.get("eventBlocks", [])

        # Blocked events
        blocked = set()
        for block in blocks_raw:
            if block.get("state") == "blocked":
                blocked.add(block.get("id"))

        # Factors by event
        factors_by_event: Dict[int, Dict[str, float]] = {}
        for f in factors_raw:
            eid = f.get("e")
            fid = f.get("f")
            val = f.get("v")
            if eid and fid and val and val > 1.0:
                if eid not in factors_by_event:
                    factors_by_event[eid] = {}
                market_name = self.FACTOR_MAP.get(fid)
                if market_name:
                    factors_by_event[eid][market_name] = val

                # Тоталы и форы (с параметром)
                param = f.get("p")  # параметр (напр. 2.5 для тотала)
                if fid == 930 and param is not None:
                    factors_by_event[eid][f"ТБ({param})"] = val
                elif fid == 931 and param is not None:
                    factors_by_event[eid][f"ТМ({param})"] = val
                elif fid == 927 and param is not None:
                    factors_by_event[eid][f"Ф1({param})"] = val
                elif fid == 928 and param is not None:
                    factors_by_event[eid][f"Ф2({param})"] = val

        # Build matches
        matches = []
        for event in events_raw:
            eid = event.get("id", 0)
            if eid in blocked:
                continue

            sport_id = event.get("sportId", 0)
            sport = self.SPORT_MAP.get(sport_id)
            if not sport:
                continue

            team1 = event.get("team1", "").strip()
            team2 = event.get("team2", "").strip()
            if not team1 or not team2:
                continue

            start_ts = event.get("startTime", 0)
            league = event.get("name", event.get("sportName", ""))

            odds = factors_by_event.get(eid, {})
            if not odds:
                continue

            match = RuMatch(
                id=f"fonbet_{eid}",
                sport=sport,
                league=league,
                home_team=team1,
                away_team=team2,
                start_time=datetime.fromtimestamp(start_ts) if start_ts else datetime.now(),
                is_live=is_live,
                odds=odds,
                bookmaker=Bookmaker.FONBET,
                fonbet_event_id=eid,
                sport_id=sport_id,
            )
            matches.append(match)

        logger.info(f"Fonbet: parsed {len(matches)} {'live' if is_live else 'prematch'} events")
        return matches

    def is_top_league(self, match: RuMatch) -> bool:
        """Матч из топ-лиги (низкая маржа)?"""
        top = self.TOP_LEAGUES.get(match.sport, [])
        return any(t.lower() in match.league.lower() for t in top)

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None


# ═══════════════════════════════════════════════════════════
#  1XSTAVKA / MELBET PARSER (аналогичная структура)
# ═══════════════════════════════════════════════════════════

class XstavkaParser:
    """
    1xСтавка (легальная версия 1xBet в России).
    API аналогично Фонбету — JSON с events/factors.
    
    Также подходит для Melbet (клон 1xBet).
    """

    def __init__(self, bookmaker: Bookmaker = Bookmaker.XSTAVKA):
        self.bookmaker = bookmaker
        self._base_url = self._get_base_url()

    def _get_base_url(self) -> str:
        if self.bookmaker == Bookmaker.XSTAVKA:
            return "https://1xstavka.ru"
        elif self.bookmaker == Bookmaker.MELBET:
            return "https://melbet.org"
        return ""

    async def fetch_line(self, sport_id: int = 1) -> List[RuMatch]:
        """Placeholder — структура аналогична Фонбету"""
        # 1xBet/1xСтавка использует похожий JSON API
        # Endpoint: /LineFeed/Get1x2_VZip?sports={sport_id}&count=50
        logger.info(f"{self.bookmaker.value}: fetch_line placeholder")
        return []


# ═══════════════════════════════════════════════════════════
#  ODDSCORP API (агрегатор — платный)
# ═══════════════════════════════════════════════════════════

class OddscorpClient:
    """
    ODDSCORP (oddscorp.com) — API агрегатор русских БК.
    Парсит: Фонбет, 1xСтавка, Олимп, Winline, Betcity, Leon и др.
    
    Преимущества:
    - Скорость <1 сек на запрос
    - Сравнение кф между БК в реальном времени
    - Поиск вилок и валуев автоматически
    - WebSocket для live обновлений
    
    Цена: от $50/мес (тестовый период 7 дней бесплатно)
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.base_url = "https://api.oddscp.com"

    async def get_forks(self, bookmakers: List[str] = None) -> List[dict]:
        """Поиск вилок между БК"""
        bk = ",".join(bookmakers or ["fonbet", "1xstavka", "olimp"])
        url = f"{self.base_url}/forks"
        params = {"token": self.api_key, "bk2_name": bk}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"ODDSCORP error: {e}")
            return []

    async def get_valuebets(self, bookmakers: List[str] = None) -> List[dict]:
        """Поиск валуев (перевесных ставок)"""
        bk = ",".join(bookmakers or ["fonbet"])
        url = f"{self.base_url}/valuebets"
        params = {"token": self.api_key, "bk2_name": bk}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"ODDSCORP valuebets error: {e}")
            return []


# ═══════════════════════════════════════════════════════════
#  МУЛЬТИСПОРТ VALUE ENGINE
# ═══════════════════════════════════════════════════════════

class MultiSportValueEngine:
    """
    Поиск value-ставок по всем видам спорта.
    
    Стратегия для каждого спорта:
    
    ⚽ ФУТБОЛ (маржа 4-5% на топ):
       - Dixon-Coles модель (из V2.0)
       - xG данные (из V2.2)
       - Рынки: 1X2, ТБ/ТМ 2.5, ОЗ
    
    🏒 ХОККЕЙ (маржа 5-6%):
       - Poisson на голы (тотал 5.5)
       - Corsi/Fenwick как доп.фичи
       - Рынки: 12 (в ОТ), ТБ/ТМ 5.5, Ф1/Ф2
    
    🏀 БАСКЕТБОЛ (маржа 5-6%):
       - Elo + home advantage сильнее
       - Тоталы стабильнее исходов
       - Рынки: 12, ТБ/ТМ, Ф1/Ф2
    
    🎾 ТЕННИС (маржа 5-6%):
       - Elo по поверхности (хард/грунт/трава)
       - H2H важнее в теннисе
       - Рынки: 12, ТБ/ТМ сетов/геймов
    
    🎮 КИБЕРСПОРТ (маржа 5-7%):
       - Форма за последние 10 карт
       - Map pool advantages
       - Рынки: 12, ТБ/ТМ карт
    """

    def __init__(self):
        self.fonbet = FonbetParser()
        # Маржа по спорту для Shin removal
        self.margin_map = FonbetParser.MARGIN_BY_SPORT

    def remove_overround_shin(self, odds: Dict[str, float],
                                sport: Sport) -> Dict[str, float]:
        """
        Shin's method адаптированный под русские БК.
        
        Особенность: маржа Фонбет разная по спортам,
        поэтому используем sport-specific overround.
        """
        implied = {k: 1.0/v for k, v in odds.items() if v > 1.0}
        total_implied = sum(implied.values())
        if total_implied <= 1:
            return {k: 1.0/v for k, v in odds.items()}

        n = len(implied)
        overround = total_implied
        z = (overround - 1) / max(n - 1, 1)

        fair = {}
        for market, imp_p in implied.items():
            numerator = (
                (z**2 + 4 * (1 - z) * imp_p**2 / overround) ** 0.5 - z
            )
            denominator = 2 * (1 - z)
            if denominator > 0:
                fair[market] = numerator / denominator
            else:
                fair[market] = imp_p / overround

        # Normalize
        total_fair = sum(fair.values())
        if total_fair > 0:
            fair = {k: v / total_fair for k, v in fair.items()}
        return fair

    def find_value_bets(self, matches: List[RuMatch],
                        model_probs: Dict[str, Dict[str, float]] = None,
                        min_edge: float = 0.03) -> List[dict]:
        """
        Поиск value-ставок по всем матчам.
        
        Если model_probs не переданы — используем Shin fair probs
        и сравниваем с кф.
        """
        value_bets = []

        for match in matches:
            # Fair probabilities (Shin)
            main_odds = {}
            if match.sport in (Sport.TENNIS, Sport.ESPORTS,
                               Sport.TABLE_TENNIS, Sport.VOLLEYBALL):
                # 2-way
                for m in ["П1", "П2"]:
                    if m in match.odds:
                        main_odds[m] = match.odds[m]
            else:
                # 3-way
                for m in ["П1", "Х", "П2"]:
                    if m in match.odds:
                        main_odds[m] = match.odds[m]

            if len(main_odds) < 2:
                continue

            fair_probs = self.remove_overround_shin(main_odds, match.sport)

            # Model probs (если есть)
            m_probs = (model_probs or {}).get(match.id, {})

            for market, fair_p in fair_probs.items():
                odds_val = match.odds.get(market, 0)
                if odds_val <= 1.01:
                    continue

                # Используем модель если есть, иначе fair
                prob = m_probs.get(market, fair_p)

                # Edge
                edge = prob * odds_val - 1.0
                if edge >= min_edge:
                    value_bets.append({
                        "match": match,
                        "market": market,
                        "odds": odds_val,
                        "probability": prob,
                        "fair_probability": fair_p,
                        "edge": edge,
                        "sport": match.sport,
                        "is_top_league": self.fonbet.is_top_league(match),
                    })

        # Сортировка: топ-лиги сначала, потом по edge
        value_bets.sort(
            key=lambda x: (-x["is_top_league"], -x["edge"])
        )
        return value_bets


# ═══════════════════════════════════════════════════════════
#  EXPRESS OPTIMIZER (упор на Фонбет)
# ═══════════════════════════════════════════════════════════

class FonbetExpressOptimizer:
    """
    Оптимизация экспрессов специально под Фонбет.
    
    Ключевые особенности:
    
    1. СТРАХОВКА ЭКСПРЕССА:
       - 6+ событий, каждое с кф ≥ 1.60
       - Если 1 нога не прошла — возврат ставки!
       - Это СИЛЬНО меняет EV → строим экспрессы на 6 ног
    
    2. МУЛЬТИСПОРТ МИКСЫ:
       - Ноги из разных видов спорта = меньше корреляции
       - Футбол + Хоккей + Баскетбол = идеальный микс
    
    3. ПРАВИЛА ОТБОРА НОГ:
       - Кф каждой ноги: 1.60 - 2.20 (для страховки)
       - Мин. вероятность ноги: 50% (fair prob)
       - Разные лиги / виды спорта
       - Разное время начала (снижает корреляцию)
    
    4. КОРРЕЛЯЦИОННЫЕ ДИСКОНТЫ:
       - Ноги из одного вида спорта: ×0.93
       - Ноги из одной лиги: ×0.88
       - Ноги в один день: ×0.97
       - Каждая доп. нога: ×0.95
    """

    # Дисконты корреляции
    DISCOUNT_PER_LEG = 0.95
    DISCOUNT_SAME_SPORT = 0.93
    DISCOUNT_SAME_LEAGUE = 0.88
    DISCOUNT_SAME_DAY = 0.97

    # Лимиты
    MIN_LEG_ODDS = 1.60      # Мин для страховки Фонбет
    MAX_LEG_ODDS = 2.30      # Не берём слишком рискованные
    MIN_LEG_PROB = 0.48       # Мин вероятность ноги
    MAX_TOTAL_ODDS = 30.0     # Потолок общего кф
    MIN_LEGS_INSURANCE = 6    # Мин ног для страховки
    MAX_LEGS = 10             # Максимум ног
    PREFERRED_LEGS = [6, 7, 8]  # Оптимальное кол-во

    def build_expresses(self, value_bets: List[dict],
                        target_legs: List[int] = None) -> List[RuExpressBet]:
        """
        Собрать лучшие экспрессы из value-ставок.
        
        Стратегия:
        1. Отфильтровать ноги по кф (1.60-2.30 для страховки)
        2. Приоритет: разные виды спорта → разные лиги → разные дни
        3. Собрать комбинации по 6, 7, 8 ног
        4. Посчитать EV с учётом страховки
        5. Отсортировать по effective_ev
        """
        target_legs = target_legs or self.PREFERRED_LEGS

        # Фильтрация ног
        eligible = [
            b for b in value_bets
            if (self.MIN_LEG_ODDS <= b["odds"] <= self.MAX_LEG_ODDS
                and b["probability"] >= self.MIN_LEG_PROB)
        ]

        if len(eligible) < min(target_legs):
            logger.info(f"Not enough legs: {len(eligible)} eligible bets")
            # Пробуем без ограничения по страховке
            eligible = [
                b for b in value_bets
                if (1.30 <= b["odds"] <= 2.50
                    and b["probability"] >= 0.45)
            ]

        expresses = []
        for n_legs in target_legs:
            if len(eligible) < n_legs:
                continue

            # Жадная стратегия: максимальное разнообразие
            combo = self._select_diverse_legs(eligible, n_legs)
            if not combo:
                continue

            express = self._build_express(combo, n_legs)
            if express and express.total_odds <= self.MAX_TOTAL_ODDS:
                expresses.append(express)

        # Сортировка по effective_ev
        expresses.sort(key=lambda e: -e.effective_ev)
        return expresses

    def _select_diverse_legs(self, bets: List[dict],
                             n: int) -> List[dict]:
        """
        Выбрать N ног с максимальным разнообразием.
        
        Приоритет:
        1. Каждый вид спорта представлен (если возможно)
        2. Каждая лига уникальна
        3. Наибольший edge
        """
        selected = []
        used_matches = set()
        used_leagues = set()
        sports_count: Dict[str, int] = {}

        # Сортируем: сначала по разнообразию спорта, потом по edge
        sorted_bets = sorted(bets, key=lambda b: -b["edge"])

        for bet in sorted_bets:
            if len(selected) >= n:
                break

            match = bet["match"]
            sport = match.sport.value
            league = match.league
            match_id = match.id

            # Нельзя два рынка одного матча
            if match_id in used_matches:
                continue

            # Штраф за дубликат лиги
            if league in used_leagues and len(selected) < n - 1:
                continue

            # Максимум 2 из одного спорта (лучше разнообразие)
            if sports_count.get(sport, 0) >= 2 and len(selected) < n - 1:
                continue

            selected.append(bet)
            used_matches.add(match_id)
            used_leagues.add(league)
            sports_count[sport] = sports_count.get(sport, 0) + 1

        # Если мало — добрать без ограничений
        if len(selected) < n:
            for bet in sorted_bets:
                if len(selected) >= n:
                    break
                if bet not in selected:
                    match = bet["match"]
                    if match.id not in used_matches:
                        selected.append(bet)
                        used_matches.add(match.id)

        return selected[:n]

    def _build_express(self, legs: List[dict], n_legs: int) -> Optional[RuExpressBet]:
        """Собрать экспресс из выбранных ног"""
        if len(legs) < 2:
            return None

        # Кф и вероятности
        total_odds = 1.0
        combined_prob = 1.0
        leg_data = []

        for leg in legs:
            odds = leg["odds"]
            prob = leg["probability"]
            total_odds *= odds
            combined_prob *= prob
            leg_data.append({
                "match": leg["match"],
                "market": leg["market"],
                "odds": odds,
                "prob": prob,
                "edge": leg["edge"],
                "sport": leg["sport"].value,
                "league": leg["match"].league,
            })

        # Корреляционный дисконт
        discount = self._calc_correlation_discount(leg_data)
        adjusted_prob = combined_prob * discount

        # EV
        ev = adjusted_prob * total_odds - 1.0

        # Страховка
        insurance = (
            len(legs) >= self.MIN_LEGS_INSURANCE and
            all(leg["odds"] >= self.MIN_LEG_ODDS for leg in legs)
        )

        express = RuExpressBet(
            legs=leg_data,
            total_odds=round(total_odds, 2),
            probability=combined_prob,
            ev=ev,
            correlation_discount=discount,
            insurance_eligible=insurance,
        )
        return express

    def _calc_correlation_discount(self, legs: List[dict]) -> float:
        """Рассчитать корреляционный дисконт"""
        n = len(legs)
        discount = self.DISCOUNT_PER_LEG ** (n - 1)

        # Одинаковые виды спорта
        sports = [leg["sport"] for leg in legs]
        sport_counts = {}
        for s in sports:
            sport_counts[s] = sport_counts.get(s, 0) + 1
        for s, count in sport_counts.items():
            if count > 1:
                pairs = count * (count - 1) // 2
                discount *= self.DISCOUNT_SAME_SPORT ** pairs

        # Одинаковые лиги
        leagues = [leg["league"] for leg in legs]
        league_counts = {}
        for l in leagues:
            league_counts[l] = league_counts.get(l, 0) + 1
        for l, count in league_counts.items():
            if count > 1:
                pairs = count * (count - 1) // 2
                discount *= self.DISCOUNT_SAME_LEAGUE ** pairs

        return round(discount, 4)

    def optimize_for_insurance(self, value_bets: List[dict]) -> List[RuExpressBet]:
        """
        Специальный метод: собрать экспрессы ТОЛЬКО для страховки.
        6+ ног, каждая ≥ 1.60, максимальное разнообразие.
        
        Почему это выгодно:
        - Страховка = бесплатная "попытка"
        - Если 5 из 6 прошли, деньги вернутся
        - P(5 из 6 верных) часто 15-25%
        - Это сильно улучшает EV
        """
        insurance_legs = [
            b for b in value_bets
            if self.MIN_LEG_ODDS <= b["odds"] <= self.MAX_LEG_ODDS
        ]

        if len(insurance_legs) < 6:
            return []

        return self.build_expresses(
            insurance_legs,
            target_legs=[6, 7, 8]
        )


# ═══════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════

class RuBettingAssistant:
    """
    Главный оркестратор для русских БК.
    
    Workflow:
    1. Парсим линию Фонбет (прематч + лайв)
    2. Ищем value по всем видам спорта
    3. Собираем экспрессы (упор на страховку 6+)
    4. Отправляем в Telegram
    """

    def __init__(self):
        self.fonbet = FonbetParser()
        self.value_engine = MultiSportValueEngine()
        self.express_optimizer = FonbetExpressOptimizer()

    async def scan(self, include_live: bool = True) -> dict:
        """Полное сканирование"""
        # 1. Парсим линию
        prematch = await self.fonbet.fetch_line(live=False)
        live = []
        if include_live:
            live = await self.fonbet.fetch_line(live=True)

        all_matches = prematch + live
        logger.info(
            f"Total: {len(all_matches)} matches "
            f"({len(prematch)} prematch, {len(live)} live)"
        )

        # Статистика по спортам
        by_sport = {}
        for m in all_matches:
            s = m.sport.value
            by_sport[s] = by_sport.get(s, 0) + 1

        # 2. Value bets (CPU bound)
        value_bets = await asyncio.to_thread(
            self.value_engine.find_value_bets,
            all_matches, model_probs=None, min_edge=0.03
        )
        logger.info(f"Value bets found: {len(value_bets)}")

        # 3. Экспрессы (CPU bound)
        final_expresses = await asyncio.to_thread(
            self._build_expresses_sync, value_bets
        )

        logger.info(
            f"Expresses: {len(final_expresses)} "
            f"(insurance: {sum(1 for e in final_expresses if e.insurance_eligible)})"
        )

        return {
            "matches": len(all_matches),
            "raw_matches": all_matches,
            "by_sport": by_sport,
            "value_bets": value_bets[:20],
            "expresses": final_expresses[:10],
            "insurance_expresses": [
                e for e in final_expresses if e.insurance_eligible
            ][:5],
            "timestamp": datetime.now().isoformat(),
        }

    def _build_expresses_sync(self, value_bets: List[dict]) -> List[RuExpressBet]:
        """CPU-bound express construction"""
        all_expresses = self.express_optimizer.build_expresses(value_bets)
        insurance_expresses = self.express_optimizer.optimize_for_insurance(value_bets)

        # Merge & Deduplicate
        seen = set()
        final = []
        for e in insurance_expresses + all_expresses:
            key = tuple(sorted(leg["match"].id + leg["market"] for leg in e.legs))
            if key not in seen:
                seen.add(key)
                final.append(e)
        return final

    async def format_telegram_report(self, result: dict) -> str:
        """Форматирование отчёта для Telegram"""
        lines = [
            "🤖 *СКАНИРОВАНИЕ ФОНБЕТ*",
            f"📊 Матчей: {result['matches']}",
            f"🎯 Валуев: {len(result['value_bets'])}",
            f"🔥 Экспрессов: {len(result['expresses'])}",
            "",
        ]

        # Спорты
        by_sport = result.get("by_sport", {})
        sport_line = " | ".join(
            f"{k}: {v}" for k, v in sorted(by_sport.items(), key=lambda x: -x[1])
        )
        lines.append(f"📋 {sport_line}")
        lines.append("")

        # Топ value bets
        if result["value_bets"]:
            lines.append("🎯 *ТОП ВАЛУИ:*")
            for i, vb in enumerate(result["value_bets"][:5], 1):
                m = vb["match"]
                lines.append(
                    f"{i}. {m.display_name}\n"
                    f"   {vb['market']} @ {vb['odds']:.2f} "
                    f"(P={vb['probability']:.0%}, edge={vb['edge']:+.1%})"
                )
            lines.append("")

        # Экспрессы со страховкой
        ins = result.get("insurance_expresses", [])
        if ins:
            lines.append("🛡️ *ЭКСПРЕССЫ СО СТРАХОВКОЙ:*")
            for i, e in enumerate(ins[:3], 1):
                lines.append(e.to_telegram())
                lines.append("")

        return "\n".join(lines)

    async def close(self):
        await self.fonbet.close()
