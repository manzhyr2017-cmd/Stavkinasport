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

from config.settings import betting_config, api_config
from core.bankroll import BankrollManager
from core.models import ExpressBet, SystemBet, ValueSignal, Match, Market, BetOutcome, ConfidenceLevel, ExpressLeg
from core.prediction_models import DixonColesModel, EloRatingSystem, EnsemblePredictor
from core.value_engine import ValueBettingEngine
from data.odds_fetcher import OddsDataFetcher
from core.ai_analyzer import AIAnalyzer
from core.bravo_api import BravoNewsFetcher

try:
    from core.live_monitor import LSTMLinePredictor, OddsTimeSeriesCollector
except ImportError:
    LSTMLinePredictor, OddsTimeSeriesCollector = None, None

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
        self.dixon_coles.load()
        self.elo = EloRatingSystem()
        self.elo.load()
        
        # Ensemble handles its own calibrator and catboost
        self.ensemble = EnsemblePredictor(self.dixon_coles, self.elo)
        self.engine = ValueBettingEngine(self.ensemble)

        # Live Monitoring (Stavki3)
        self.timeseries = OddsTimeSeriesCollector() if OddsTimeSeriesCollector else None
        self.lstm_predictor = LSTMLinePredictor() if LSTMLinePredictor else None

        self._prev_odds_cache: dict = {}
        self._signals_today: List[ValueSignal] = []
        
        # Load placed bets from history to prevent duplicates on restart
        self._placed_keys: set = {b.match_info for b in self.bankroll.bet_history}
        logger.info(f"Loaded {len(self._placed_keys)} placed bets from history")

        # RU Mode
        self.ru_assistant = None
        if api_config.RU_MODE:
            try:
                from core.ru_bookmakers import RuBettingAssistant
                self.ru_assistant = RuBettingAssistant()
                logger.info("🇷🇺 Russian Betting Assistant (Fonbet) Enabled")
            except ImportError:
                logger.error("❌ Could not import RuBettingAssistant")

        # AI Analyst & News
        self.ai_analyzer = AIAnalyzer()
        self.news_fetcher = BravoNewsFetcher()

    async def run_scan(self) -> dict:
        """Полное сканирование рынка"""
        logger.info("🔍 V2 Scan starting...")

        matches_count = 0
        all_signals: List[ValueSignal] = []
        all_expresses: List[ExpressBet] = []  # To store pre-calculated expresses (e.g. from RU mode)
        all_express_legs: List[ValueSignal] = []

        # 1. Fetch international odds (OddsAPI)
        if api_config.ODDS_API_KEY:
            try:
                all_matches = await self.fetcher.fetch_all_sports_odds()
                matches_count += sum(len(v) for v in all_matches.values())
                
                for sport, matches in all_matches.items():
                    signals = self.engine.find_value_bets(matches)
                    all_signals.extend(signals)
                    
                    # Find legs specifically for expresses (relaxed edge)
                    exp_legs = self.engine.find_express_candidates(matches)
                    all_express_legs.extend(exp_legs)

                    # Line movement & LSTM (keep existing logic)
                    for match in matches:
                        key = match.id
                        if self.timeseries:
                            self.timeseries.record(key, match.avg_odds)
                        if key in self._prev_odds_cache:
                            mvs = ValueBettingEngine.detect_line_movement(
                                self._prev_odds_cache[key], match.best_odds
                            )
                            for mv in mvs:
                                if mv["is_sharp_move"]:
                                    logger.info(f"⚡ Sharp move: {match.home_team} vs {match.away_team} {mv['change_pct']:+.1f}%")
                        self._prev_odds_cache[key] = match.best_odds

            except Exception as e:
                logger.error(f"International scan error: {e}")

        # 2. RU MODE (Fonbet)
        if self.ru_assistant:
            try:
                ru_res = await self.ru_assistant.scan()
                matches_count += ru_res.get("matches", 0)
                
                # Convert Value Bets
                ru_values = self._convert_ru_values(ru_res.get("value_bets", []))
                # Convert Expresses
                ru_expresses = self._convert_ru_expresses(ru_res.get("expresses", []))
                
                # Prioritize RU signals (add to start)
                all_signals = ru_values + all_signals
                all_expresses.extend(ru_expresses) 
                
                logger.info(f"🇷🇺 RU Stats: {len(ru_values)} signals, {len(ru_expresses)} expresses")
                
            except Exception as e:
                logger.error(f"RU scan error: {e}", exc_info=True)
         
        logger.info(f"📊 Total Matches: {matches_count} | Signals: {len(all_signals)}")

        if self.lstm_predictor and self.timeseries:
            for s in all_signals:
                series = self.timeseries.to_numpy(s.match.id, outcome=s.outcome.value)
                if series is not None:
                    s.lstm_prediction = self.lstm_predictor.predict_next_odds(series)

        logger.info(f"🎯 {len(all_signals)} value bets found")



        # 3. Calculate stakes
        # 3. Calculate stakes
        for s in all_signals:
            stake = self.bankroll.kelly_single(s)
            s.stake_amount = stake
            s.kelly_stake = (
                stake / self.bankroll.bankroll if self.bankroll.bankroll > 0 else 0
            )

        active = []
        enrichment_tasks = []

        for s in all_signals:
            if s.stake_amount > 0 and s.edge >= betting_config.MIN_VALUE_EDGE:
                # Создаем задачу на обогащение (новости + AI)
                enrichment_tasks.append(self._enrich_signal(s))
                active.append(s)
        
        if enrichment_tasks:
            logger.info(f"🧠 Enriching {len(enrichment_tasks)} signals with AI & News...")
            await asyncio.gather(*enrichment_tasks)
        
        # AUTO-BET LOGIC
        # AUTO-BET LOGIC
        if betting_config.AUTO_BET:
             # Сортируем по EV (edge * kelly)
             active.sort(key=lambda x: x.edge * x.kelly_stake, reverse=True)
             
             max_bets = getattr(betting_config, "AUTO_BET_MAX_PER_SCAN", 5)
             candidates = active[:max_bets]
             
             for s in candidates:
                 # Deduplicate using match_info string (consistent with bankroll history)
                 info = f"{s.match.home_team} vs {s.match.away_team} ({s.outcome.value})"
                 if info in self._placed_keys:
                     continue
                 
                 # Check Strategy Rules (already in analysis)
                 # Place bet
                 try:
                     # 1. Record in Bankroll (Persisted)
                     self.bankroll.record_bet(s.id, "single", s.stake_amount, s.bookmaker_odds, match_info=info)
                     self._placed_keys.add(info)
                     
                     # 2. Mark visual status
                     s.status = "placed"
                     s.bookmaker_name += " ✅ AUTO"
                     
                     # 3. Notify Telegram (Text Mode)
                     if self.notifier:
                         msg = (
                             f"🤖 <b>АВТО-СТАВКА ВЫПОЛНЕНА</b>\n"
                             f"{s.to_telegram_message()}"
                         )
                         await self.notifier.send_text(msg)
                         await asyncio.sleep(2.0)
                         
                     logger.info(f"✅ Auto-bet placed: {info}")
                     
                 except Exception as e:
                     logger.error(f"Auto-bet error: {e}")


        # 4. Build expresses (2-5 legs) using relaxed candidates

        
        for n in [2, 3, 4, 5]:
            exps = self.engine.build_express_bets(all_express_legs, num_legs=n, max_expresses=3)
            all_expresses.extend(exps)

        for e in all_expresses:
            e.stake_amount = self.bankroll.kelly_express(e)
        active_exp = [e for e in all_expresses if e.stake_amount > 0]

        # 5. Build systems
        systems = self.engine.build_system_bets(active)
        for sys in systems:
            sys.stake_per_combo = self.bankroll.kelly_system(sys)

        # 6. Notify (Only non-auto placed signals?)
        # If auto-placed, we already sent text notification.
        # But maybe we still want full signal card? 
        # Sent signals with "placed" status might be confusing if they have "Place" button.
        # Filter out auto-placed from regular notifications to avoid duplicates?
        # NO, user might want to see full analysis. 
        # But we marked s.status="placed". Notifier should handle this?
        # Standard Notifier sends "Place/Skip" buttons.
        # Let's send only non-auto signals via standard notifier to avoid confusion.
        
        manual_signals = [s for s in active if (s.match.id, s.outcome) not in self._placed_keys]
        
        if self.notifier:
            await self._notify(manual_signals[:5], active_exp[:3], systems[:2])

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

    def _convert_ru_values(self, ru_bets: List[dict]) -> List[ValueSignal]:
        signals = []
        for b in ru_bets:
            ru_match = b["match"]
            
            # Create Dummy Match
            match = Match(
                id=ru_match.id,
                sport=ru_match.sport.value,
                league=ru_match.league,
                home_team=ru_match.home_team,
                away_team=ru_match.away_team,
                commence_time=ru_match.start_time,
                bookmaker_odds=[] 
            )
            
            # Map Market/Outcome
            market_str = b["market"]
            market = Market.H2H
            outcome = BetOutcome.HOME
            
            if "П1" in market_str: outcome = BetOutcome.HOME
            elif "П2" in market_str: outcome = BetOutcome.AWAY
            elif "Х" in market_str: outcome = BetOutcome.DRAW
            elif "ТБ" in market_str: 
                market = Market.TOTALS
                outcome = BetOutcome.OVER
            elif "ТМ" in market_str: 
                market = Market.TOTALS
                outcome = BetOutcome.UNDER
            
            conf = ConfidenceLevel.LOW
            if b.get("is_top_league"): conf = ConfidenceLevel.HIGH
            elif b["edge"] > 0.1: conf = ConfidenceLevel.MEDIUM

            vs = ValueSignal(
                match=match,
                market=market,
                outcome=outcome,
                model_probability=b["probability"],
                bookmaker_odds=b["odds"],
                bookmaker_name=b["match"].bookmaker.value if hasattr(b["match"].bookmaker, "value") else "Fonbet",
                edge=b["edge"],
                confidence_level=conf,
                status=SignalStatus.PENDING
            )
            signals.append(vs)
        return signals

    def _convert_ru_expresses(self, ru_expresses: List) -> List[ExpressBet]:
        converted = []
        for re in ru_expresses:
            legs = []
            for leg_data in re.legs:
                # leg_data is a dict from RuExpressBet.legs
                # We need to create a ValueSignal wrapper for ExpressLeg
                
                # Mock a signal
                # match, market, outcome, etc.
                ru_match = leg_data["match"]
                match = Match(
                    id=ru_match.id,
                    sport=ru_match.sport.value,
                    league=ru_match.league,
                    home_team=ru_match.home_team,
                    away_team=ru_match.away_team,
                    commence_time=ru_match.start_time,
                    bookmaker_odds=[]
                )
                
                market_str = leg_data["market"]
                market = Market.H2H
                outcome = BetOutcome.HOME
                if "П1" in market_str: outcome = BetOutcome.HOME
                elif "П2" in market_str: outcome = BetOutcome.AWAY
                elif "Х" in market_str: outcome = BetOutcome.DRAW
                elif "ТБ" in market_str: 
                    market = Market.TOTALS
                    outcome = BetOutcome.OVER
                elif "ТМ" in market_str: 
                    market = Market.TOTALS
                    outcome = BetOutcome.UNDER

                vs = ValueSignal(
                    match=match,
                    market=market,
                    outcome=outcome,
                    model_probability=leg_data["prob"],
                    bookmaker_odds=leg_data["odds"],
                    bookmaker_name="Fonbet",
                    edge=leg_data.get("edge", 0),
                    confidence_level=ConfidenceLevel.HIGH, # Express legs are filtered
                    status=SignalStatus.PENDING
                )
                
                exp_leg = ExpressLeg(
                    signal=vs,
                    odds=leg_data["odds"],
                    probability=leg_data["prob"],
                    edge=leg_data.get("edge", 0)
                )
                legs.append(exp_leg)
            
            analysis = ""
            if re.insurance_eligible:
                analysis = "🛡️ СТРАХОВКА (Возврат при 1 проигрыше)"
            
            eb = ExpressBet(
                legs=legs,
                correlation_discount=re.correlation_discount,
                stake_amount=re.stake,
                status=SignalStatus.PENDING,
                analysis=analysis
            )
            converted.append(eb)
            
        return converted

    async def _enrich_signal(self, s: ValueSignal):
        """
        Дополняет сигнал новостями и AI-анализом (NVIDIA NIM).
        """
        try:
            # 1. Сбор новостей (Bravo/Brave)
            query = f"{s.match.home_team} {s.match.away_team} football news injuries"
            news = await self.news_fetcher.get_latest_news(query)
            
            # 2. Генерация анализа через AI (Llama-3 через NVIDIA NIM)
            ai_text = await self.ai_analyzer.generate_analysis(s, news)
            
            # 3. Технические детали
            tech_reasons = []
            if s.edge >= 0.05: tech_reasons.append(f"🔥 Высокий перевес: +{s.edge:.1%}")
            if s.sharp_agrees: tech_reasons.append("🎯 Консенсус с острыми линиями")
            if s.lstm_prediction: 
                trend = "ВВЕРХ" if s.lstm_prediction.get('expected_move', 0) > 0 else "ВНИЗ"
                tech_reasons.append(f"📈 LSTM тренд: {trend}")
            
            tech_str = "\n".join(tech_reasons)
            s.analysis = f"{ai_text}\n\n{tech_str}" if tech_str else ai_text
            
        except Exception as e:
            logger.error(f"Enrichment failed for {s.id}: {e}")
            s.analysis = "Технический анализ: Ставка подтверждена математической моделью."

    def _generate_analysis(self, s: ValueSignal) -> str:
        reasons = []
        if s.edge >= 0.1: 
            reasons.append(f"🤯 HUGE Edge: {s.edge:.1%}")
        elif s.edge >= 0.05: 
            reasons.append(f"🔥 High Edge: {s.edge:.1%}")
        elif s.edge >= 0.02: 
            reasons.append(f"✅ Positive Edge: {s.edge:.1%}")
        
        if "Fonbet" in s.bookmaker_name:
            reasons.append("🇷🇺 RU Market (Fonbet)")
            
        if s.kelly_stake >= 0.05:
            reasons.append(f"💰 Strong Kelly: {s.kelly_stake:.1%}")
        elif s.kelly_stake > 0.02:
             reasons.append(f"💵 Kelly Stake: {s.kelly_stake:.1%}")

        if s.sharp_agrees:
             reasons.append("🎯 Sharp Bookmakers Agree")
             
        conf = "Low"
        if hasattr(s.confidence_level, "name"):
            conf = s.confidence_level.name
        elif hasattr(s.confidence_level, "value"):
            conf = s.confidence_level.value
            
        reasons.append(f"🔍 Confidence: {conf}")

        return "\n".join(reasons)
