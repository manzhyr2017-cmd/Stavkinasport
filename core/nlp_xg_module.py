"""
=============================================================================
 V2.2 — NLP INJURY/NEWS SCANNER + xG DATA FETCHER

 Модули:
   1. FBRef xG Scraper — скачивает xG/xGA для топ-5 лиг
   2. InjuryScanner — мониторинг травм из новостных RSS
   3. TeamContextBuilder — объединяет все внешние данные в фичи

 Ключевые библиотеки:
   - soccerdata: FBRef, Understat, ClubElo (pip install soccerdata)
   - penaltyblog: Dixon-Coles optimized + скраперы (pip install penaltyblog)
=============================================================================
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  FBREF / XG DATA FETCHER
# ═══════════════════════════════════════════════════════════

class XGDataFetcher:
    """
    Получение xG-данных из FBRef/Understat.
    
    Стратегия:
    1. soccerdata (лучший вариант — единый API)
    2. pandas.read_html как fallback (простой scrape)
    3. Understat API (JSON, нет rate limits)
    
    Данные: xG, xGA, Possession, SoT, Corners для каждого матча.
    """

    # FBRef league IDs
    FBREF_LEAGUES = {
        "EPL": {"id": 9, "slug": "Premier-League"},
        "La_Liga": {"id": 12, "slug": "La-Liga"},
        "Bundesliga": {"id": 20, "slug": "Bundesliga"},
        "Serie_A": {"id": 11, "slug": "Serie-A"},
        "Ligue_1": {"id": 13, "slug": "Ligue-1"},
    }

    # Understat league slugs
    UNDERSTAT_LEAGUES = ["EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1"]

    def __init__(self):
        self._cache: Dict[str, pd.DataFrame] = {}

    async def fetch_xg_soccerdata(self, league: str = "ENG-Premier League",
                                   seasons: List[str] = None) -> pd.DataFrame:
        """
        Метод 1: soccerdata (pip install soccerdata)
        
        Возвращает DataFrame с xG, xGA, результатами для каждого матча.
        """
        try:
            import soccerdata as sd
            seasons = seasons or ["2024", "2023", "2022"]
            fbref = sd.FBref(league, seasons)
            schedule = fbref.read_schedule()

            df = schedule.reset_index()
            df = df.rename(columns={
                "home_team": "home", "away_team": "away",
                "home_xg": "home_xg", "away_xg": "away_xg",
                "score": "score",
            })
            logger.info(
                f"soccerdata: {len(df)} matches loaded for {league}"
            )
            return df
        except ImportError:
            logger.warning("soccerdata not installed, trying fallback")
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"soccerdata error: {e}")
            return pd.DataFrame()

    def fetch_xg_fbref_direct(self, league: str = "EPL",
                               season: str = "2024-2025") -> pd.DataFrame:
        """
        Метод 2: прямой скрапинг FBRef через pandas.read_html()
        
        URL: fbref.com/en/comps/{id}/{season}/schedule/...
        """
        cfg = self.FBREF_LEAGUES.get(league)
        if not cfg:
            return pd.DataFrame()

        url = (
            f"https://fbref.com/en/comps/{cfg['id']}/{season}/"
            f"schedule/{season}-{cfg['slug']}-Scores-and-Fixtures"
        )

        try:
            tables = pd.read_html(url)
            if not tables:
                return pd.DataFrame()

            df = tables[0]
            df = df[df["Wk"].notna()]  # Убираем пустые строки

            result = pd.DataFrame()
            result["home"] = df.get("Home", "")
            result["away"] = df.get("Away", "")
            result["date"] = pd.to_datetime(df.get("Date", ""))
            result["home_xg"] = pd.to_numeric(df.get("xG", 0), errors="coerce")
            result["away_xg"] = pd.to_numeric(df.get("xG.1", 0), errors="coerce")
            result["score"] = df.get("Score", "")

            # Parse score
            for idx, row in result.iterrows():
                score = str(row.get("score", ""))
                parts = score.split("–") if "–" in score else score.split("-")
                if len(parts) == 2:
                    try:
                        result.at[idx, "home_goals"] = int(parts[0].strip())
                        result.at[idx, "away_goals"] = int(parts[1].strip())
                    except ValueError:
                        pass

            result["league"] = league
            self._cache[f"{league}_{season}"] = result
            logger.info(f"FBRef direct: {len(result)} matches for {league} {season}")
            return result

        except Exception as e:
            logger.warning(f"FBRef scrape error for {league}: {e}")
            return pd.DataFrame()

    async def fetch_understat(self, league: str = "EPL",
                               season: str = "2024") -> pd.DataFrame:
        """
        Метод 3: Understat JSON API (без rate limits!)
        
        Understat хранит JSON прямо в HTML <script> тегах.
        """
        try:
            import aiohttp
            import json

            url = f"https://understat.com/league/{league}/{season}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    html = await resp.text()

            # Extract JSON from script tag
            pattern = r"var\s+datesData\s*=\s*JSON\.parse\('(.+?)'\)"
            match = re.search(pattern, html)
            if not match:
                return pd.DataFrame()

            data_str = match.group(1).encode().decode("unicode_escape")
            data = json.loads(data_str)

            rows = []
            for match_data in data:
                rows.append({
                    "home": match_data.get("h", {}).get("title", ""),
                    "away": match_data.get("a", {}).get("title", ""),
                    "home_goals": int(match_data.get("goals", {}).get("h", 0)),
                    "away_goals": int(match_data.get("goals", {}).get("a", 0)),
                    "home_xg": float(match_data.get("xG", {}).get("h", 0)),
                    "away_xg": float(match_data.get("xG", {}).get("a", 0)),
                    "date": match_data.get("datetime", ""),
                    "league": league,
                })

            df = pd.DataFrame(rows)
            logger.info(f"Understat: {len(df)} matches for {league} {season}")
            return df

        except Exception as e:
            logger.warning(f"Understat error: {e}")
            return pd.DataFrame()

    def get_team_xg_stats(self, df: pd.DataFrame, team: str,
                          last_n: int = 10) -> dict:
        """Агрегированная xG-статистика команды"""
        mask = (df["home"] == team) | (df["away"] == team)
        recent = df[mask].sort_values("date", ascending=False).head(last_n)

        xg_for = xg_against = 0.0
        for _, row in recent.iterrows():
            if row["home"] == team:
                xg_for += row.get("home_xg", 0) or 0
                xg_against += row.get("away_xg", 0) or 0
            else:
                xg_for += row.get("away_xg", 0) or 0
                xg_against += row.get("home_xg", 0) or 0

        n = len(recent) or 1
        return {
            "xg_per_game": round(xg_for / n, 2),
            "xga_per_game": round(xg_against / n, 2),
            "xg_diff": round((xg_for - xg_against) / n, 2),
            "matches": n,
        }


# ═══════════════════════════════════════════════════════════
#  NLP INJURY / NEWS SCANNER
# ═══════════════════════════════════════════════════════════

class InjuryScanner:
    """
    Мониторинг травм и важных новостей из RSS/HTML.
    
    Стратегия:
    1. RSS feeds (BBC Sport, Sky Sports, Guardian)
    2. Keyword extraction (имена ключевых игроков)
    3. Impact scoring (звёздность + позиция)
    
    Ключевые слова: injury, injured, out, ruled out, doubt, 
    suspended, ban, fitness, return, available
    """

    INJURY_KEYWORDS = [
        "injury", "injured", "out for", "ruled out", "sidelined",
        "doubt", "doubtful", "suspended", "ban", "red card",
        "hamstring", "knee", "ankle", "muscle", "ACL", "MCL",
        "concussion", "fracture", "surgery",
    ]
    RETURN_KEYWORDS = [
        "return", "returns", "available", "fit", "back in training",
        "passed fitness test", "in contention",
    ]

    # Key players impact scores (примерные — обновлять вручную)
    KEY_PLAYERS = {
        "Haaland": {"team": "Man City", "impact": 0.9, "position": "ST"},
        "Salah": {"team": "Liverpool", "impact": 0.85, "position": "RW"},
        "Saka": {"team": "Arsenal", "impact": 0.85, "position": "RW"},
        "Mbappé": {"team": "Real Madrid", "impact": 0.9, "position": "LW"},
        "Vinicius": {"team": "Real Madrid", "impact": 0.85, "position": "LW"},
        "Kane": {"team": "Bayern", "impact": 0.9, "position": "ST"},
        "De Bruyne": {"team": "Man City", "impact": 0.85, "position": "CM"},
        "Bellingham": {"team": "Real Madrid", "impact": 0.8, "position": "AM"},
        "Lautaro": {"team": "Inter", "impact": 0.85, "position": "ST"},
        "Gyökeres": {"team": "Sporting CP", "impact": 0.9, "position": "ST"},
        "Icardi": {"team": "Galatasaray", "impact": 0.85, "position": "ST"},
        "Osimhen": {"team": "Galatasaray", "impact": 0.85, "position": "ST"},
        "Estevão": {"team": "Palmeiras", "impact": 0.8, "position": "RW"},
    }

    def __init__(self):
        self._injury_reports: Dict[str, List[dict]] = {}

    async def scan_rss_feeds(self) -> List[dict]:
        """
        Сканирование RSS новостей о травмах.
        
        Возвращает:
            [{"player": ..., "team": ..., "status": "out/doubt/return",
              "impact": 0-1, "source": ..., "date": ...}]
        """
        feeds = [
            "https://feeds.bbci.co.uk/sport/football/rss.xml",
            "https://www.skysports.com/rss/11095",
            "https://www.theguardian.com/football/rss",
            "https://www.espn.com/espn/rss/football/news",
            "https://www.lequipe.fr/rss/Football/Ligue-1/flux.xml",
            "https://as.com/rss/futbol/primera.xml",
        ]

        results = []
        try:
            import aiohttp
            import xml.etree.ElementTree as ET

            async with aiohttp.ClientSession() as session:
                for feed_url in feeds:
                    try:
                        async with session.get(feed_url, timeout=10) as resp:
                            text = await resp.text()
                        root = ET.fromstring(text)
                        items = root.findall(".//item")

                        for item in items[:20]:
                            title = (item.findtext("title") or "").lower()
                            desc = (item.findtext("description") or "").lower()
                            content = title + " " + desc
                            link = item.findtext("link") or ""

                            report = self._parse_injury_report(content, link)
                            if report:
                                results.append(report)
                    except Exception as e:
                        logger.debug(f"Feed error {feed_url}: {e}")

        except ImportError:
            logger.warning("aiohttp not available for RSS scanning")

        logger.info(f"InjuryScanner: {len(results)} reports found")
        return results

    def _parse_injury_report(self, text: str, source: str) -> Optional[dict]:
        """Parse текст на предмет травм/возвращений"""
        # Check for injury keywords
        is_injury = any(kw in text for kw in self.INJURY_KEYWORDS)
        is_return = any(kw in text for kw in self.RETURN_KEYWORDS)

        if not is_injury and not is_return:
            return None

        # Find player names
        for player_name, info in self.KEY_PLAYERS.items():
            if player_name.lower() in text:
                status = "return" if is_return else "out"
                if "doubt" in text or "doubtful" in text:
                    status = "doubt"

                report = {
                    "player": player_name,
                    "team": info["team"],
                    "position": info["position"],
                    "status": status,
                    "impact": info["impact"],
                    "source": source,
                    "date": datetime.utcnow().isoformat(),
                }

                # Cache
                team = info["team"]
                if team not in self._injury_reports:
                    self._injury_reports[team] = []
                self._injury_reports[team].append(report)

                return report
        return None

    def get_team_injury_impact(self, team: str) -> float:
        """
        Суммарный impact травм на команду.
        
        Returns:
            0.0 = никаких травм, 1.0 = катастрофа
        """
        reports = self._injury_reports.get(team, [])
        recent = [
            r for r in reports
            if (datetime.utcnow() - datetime.fromisoformat(r["date"])) 
            < timedelta(days=7)
        ]

        total_impact = 0.0
        for r in recent:
            if r["status"] == "out":
                total_impact += r["impact"]
            elif r["status"] == "doubt":
                total_impact += r["impact"] * 0.5
            elif r["status"] == "return":
                total_impact -= r["impact"] * 0.3  # Возвращение = бонус

        return max(0.0, min(total_impact, 1.0))

    def get_injury_adjustment(self, home: str, away: str) -> dict:
        """
        Корректировка вероятностей на основе травм.
        
        Если у Home команды ключевые травмы → P(home) снижается.
        """
        home_impact = self.get_team_injury_impact(home)
        away_impact = self.get_team_injury_impact(away)

        # Каждый 0.1 impact ≈ 1.5% сдвиг вероятности
        home_adj = -home_impact * 0.15 + away_impact * 0.15
        away_adj = -away_impact * 0.15 + home_impact * 0.15

        return {
            "home_prob_adj": round(home_adj, 3),
            "away_prob_adj": round(away_adj, 3),
            "home_injuries": home_impact,
            "away_injuries": away_impact,
            "net_advantage": round(away_impact - home_impact, 3),
        }


# ═══════════════════════════════════════════════════════════
#  TEAM CONTEXT BUILDER (объединяет всё)
# ═══════════════════════════════════════════════════════════

class TeamContextBuilder:
    """Объединяет xG, травмы, форму в единый контекст для матча"""

    def __init__(self):
        self.xg_fetcher = XGDataFetcher()
        self.injury_scanner = InjuryScanner()

    async def build_context(self, home: str, away: str,
                            xg_data: pd.DataFrame = None) -> dict:
        """Полный контекст для матча"""
        context = {"home": home, "away": away}

        # xG
        if xg_data is not None and len(xg_data) > 0:
            context["home_xg"] = self.xg_fetcher.get_team_xg_stats(
                xg_data, home
            )
            context["away_xg"] = self.xg_fetcher.get_team_xg_stats(
                xg_data, away
            )

        # Injuries
        context["injuries"] = self.injury_scanner.get_injury_adjustment(
            home, away
        )

        return context
