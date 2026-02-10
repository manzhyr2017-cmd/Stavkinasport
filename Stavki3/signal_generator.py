"""
=============================================================================
 BETTING ASSISTANT V2 — SIGNAL GENERATOR
 Оркестратор: данные → модели → аналитика → банкролл → Telegram
=============================================================================
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from config.settings import betting_config
from core.bankroll import BankrollManager
from core.models import ExpressBet, SystemBet, ValueSignal
from core.prediction_models import DixonColesModel, EloRatingSystem, EnsemblePredictor
from core.value_engine import ValueBettingEngine
from data.odds_fetcher import OddsDataFetcher

logger = logging.getLogger(__name__)


class SignalGenerator:
    def __init__(
        self,
        odds_fetcher: OddsDataFetcher = None,
        bankroll: BankrollManager = None,
        notifier=None,
    ):
        self.fetcher = odds_fetcher or OddsDataFetcher()
        self.bankroll = bankroll or BankrollManager()
        self.notifier = notifier

        # ML Models
        self.dixon_coles = DixonColesModel()
        self.elo = EloRatingSystem()
        self.ensemble = EnsemblePredictor(self.dixon_coles, self.elo)
        self.engine = ValueBettingEngine(self.ensemble)

        self._prev_odds_cache: dict = {}
        self._signals_today: List[ValueSignal] = []

    async def run_scan(self) -> dict:
        """Полное сканирование рынка"""
        logger.info("🔍 V2 Scan starting...")

        # 1. Fetch odds
        all_matches = await self.fetcher.fetch_all_sports_odds()
        total = sum(len(v) for v in all_matches.values())
        logger.info(f"📊 {total} matches fetched")

        # 2. Find value bets
        all_signals: List[ValueSignal] = []
        for sport, matches in all_matches.items():
            signals = self.engine.find_value_bets(matches)
            all_signals.extend(signals)

            # Line movement detection
            for match in matches:
                key = match.id
                if key in self._prev_odds_cache:
                    mvs = ValueBettingEngine.detect_line_movement(
                        self._prev_odds_cache[key], match.best_odds
                    )
                    for mv in mvs:
                        if mv["is_sharp_move"]:
                            logger.info(
                                f"⚡ Sharp move: {match.home_team} vs "
                                f"{match.away_team} [{mv['outcome']}] "
                                f"{mv['change_pct']:+.1f}%"
                            )
                self._prev_odds_cache[key] = match.best_odds

        logger.info(f"🎯 {len(all_signals)} value bets found")

        # 3. Calculate stakes
        for s in all_signals:
            stake = self.bankroll.kelly_single(s)
            s.stake_amount = stake
            s.kelly_stake = (
                stake / self.bankroll.bankroll if self.bankroll.bankroll > 0 else 0
            )

        active = [s for s in all_signals if s.stake_amount > 0]

        # 4. Build expresses (2, 3, 4 legs)
        all_expresses = []
        for n in [2, 3, 4]:
            exps = self.engine.build_express_bets(active, num_legs=n, max_expresses=3)
            all_expresses.extend(exps)

        for e in all_expresses:
            e.stake_amount = self.bankroll.kelly_express(e)
        active_exp = [e for e in all_expresses if e.stake_amount > 0]

        # 5. Build systems
        systems = self.engine.build_system_bets(active)
        for sys in systems:
            sys.stake_per_combo = self.bankroll.kelly_system(sys)

        # 6. Notify
        if self.notifier:
            await self._notify(active[:5], active_exp[:3], systems[:2])

        self._signals_today.extend(active)

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "matches_scanned": total,
            "singles": active,
            "expresses": active_exp,
            "systems": systems,
            "stats": self.bankroll.get_stats(),
            "api_usage": self.fetcher.api_usage_info,
        }

    async def _notify(self, signals, expresses, systems):
        try:
            for s in signals:
                await self.notifier.send_signal(s)
                await asyncio.sleep(1.5)
            for e in expresses:
                await self.notifier.send_express(e)
                await asyncio.sleep(1.5)
            for sys in systems:
                await self.notifier.send_system(sys)
                await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"Notification error: {e}")

    async def run_continuous(self, interval: int = None):
        interval = interval or betting_config.ODDS_POLL_INTERVAL
        logger.info(f"🚀 Continuous monitoring (every {interval}s)")
        while True:
            try:
                if self.bankroll.check_stop_conditions():
                    logger.warning("⛔ Stop active — pausing 1h")
                    await asyncio.sleep(3600)
                    continue
                await self.run_scan()
            except Exception as e:
                logger.error(f"Scan error: {e}", exc_info=True)
            await asyncio.sleep(interval)
