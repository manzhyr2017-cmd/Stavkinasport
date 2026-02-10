"""
=============================================================================
 BETTING ASSISTANT V2 — VALUE ENGINE
 
 Полностью переработанный движок:
 1. 4 метода снятия маржи (Shin, Power, Additive, Multiplicative)
 2. Correlation-aware экспрессы (штраф за зависимые ноги)
 3. Фильтр по «шарповому» БК (Pinnacle)
 4. Система доверия (сигнал подтверждён X моделями)
=============================================================================
"""
import logging
import math
from datetime import datetime
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from config.settings import betting_config
from core.models import (
    BetOutcome, ConfidenceLevel, ExpressBet, ExpressLeg,
    Market, Match, SignalStatus, SystemBet, ValueSignal,
)

logger = logging.getLogger(__name__)


class ValueBettingEngine:

    def __init__(self, ensemble_predictor=None):
        self.predictor = ensemble_predictor
        self.cfg = betting_config
        
        try:
            from core.live_monitor import SharpMoneyDetector
            from core.nlp_xg_module import InjuryScanner
            self.sharp_detector = SharpMoneyDetector()
            self.injury_scanner = InjuryScanner()
        except ImportError:
            self.sharp_detector = None
            self.injury_scanner = None

    # ═══════════════════════════════════════════════════════
    #  OVERROUND REMOVAL (4 метода)
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def remove_overround_basic(odds: Dict[str, float]) -> Dict[str, float]:
        """
        Метод 1: Basic Normalization.
        P_fair(x) = (1/odds_x) / sum(1/odds_i)
        Просто и грубо — не учитывает favourite-longshot bias.
        """
        implied = {k: 1.0 / v for k, v in odds.items() if v > 0}
        total = sum(implied.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in implied.items()}

    @staticmethod
    def remove_overround_multiplicative(odds: Dict[str, float]) -> Dict[str, float]:
        """
        Метод 2: Multiplicative (равномерное снятие маржи).
        P_fair(x) = (1/odds_x) / overround
        
        Лучше basic — маржа снимается пропорционально.
        """
        implied = {k: 1.0 / v for k, v in odds.items() if v > 0}
        overround = sum(implied.values())
        if overround <= 0:
            return {}
        return {k: v / overround for k, v in implied.items()}

    @staticmethod
    def remove_overround_power(odds: Dict[str, float]) -> Dict[str, float]:
        """
        Метод 3: Power Method.
        Находим k такое, что sum((1/odds_i)^k) = 1.
        
        Источник: Keith Cheung (2015) — лучше учитывает 
        favourite-longshot bias, чем basic/multiplicative.
        """
        implied = {k: 1.0 / v for k, v in odds.items() if v > 0}
        if not implied:
            return {}

        # Бинарный поиск для k
        lo, hi = 0.5, 2.0
        for _ in range(100):
            mid = (lo + hi) / 2
            total = sum(p ** mid for p in implied.values())
            if total > 1.0:
                lo = mid
            else:
                hi = mid
            if abs(total - 1.0) < 1e-8:
                break

        k = (lo + hi) / 2
        fair = {name: p ** k for name, p in implied.items()}
        total = sum(fair.values())
        return {name: v / total for name, v in fair.items()}

    @staticmethod
    def remove_overround_shin(odds: Dict[str, float]) -> Dict[str, float]:
        """
        Метод 4: Shin's Method.
        Учитывает долю «инсайдерских» ставок (z).
        
        Shin (1993) — самый точный метод,
        но сложнее вычислительно.
        
        z = (overround - 1) / (n - 1)   (приближение)
        p_fair = (sqrt(z^2 + 4*(1-z)*implied^2/overround) - z) / (2*(1-z))
        """
        n = len(odds)
        if n < 2:
            return ValueBettingEngine.remove_overround_basic(odds)

        implied = {k: 1.0 / v for k, v in odds.items() if v > 0}
        overround = sum(implied.values())

        if overround <= 1.0:
            return implied  # Нет маржи — уже честные

        z = (overround - 1.0) / (n - 1.0)  # Доля инсайдерских ставок

        fair = {}
        for name, p in implied.items():
            discriminant = z ** 2 + 4 * (1 - z) * (p ** 2) / overround
            if discriminant < 0:
                fair[name] = p / overround
            else:
                fair[name] = (math.sqrt(discriminant) - z) / (2 * (1 - z))

        # Нормализация
        total = sum(fair.values())
        if total > 0:
            fair = {k: v / total for k, v in fair.items()}
        return fair

    def get_fair_probabilities(self, odds: Dict[str, float],
                                method: str = "shin") -> Dict[str, float]:
        """Выбор метода снятия маржи"""
        methods = {
            "basic": self.remove_overround_basic,
            "multiplicative": self.remove_overround_multiplicative,
            "power": self.remove_overround_power,
            "shin": self.remove_overround_shin,
        }
        fn = methods.get(method, self.remove_overround_shin)
        return fn(odds)

    # ═══════════════════════════════════════════════════════
    #  FIND VALUE BETS
    # ═══════════════════════════════════════════════════════

    def find_value_bets(self, matches: List[Match]) -> List[ValueSignal]:
        """Основной поиск value bets с multi-model confirmation"""
        signals = []
        for match in matches:
            match_signals = self._analyze_match(match)
            signals.extend(match_signals)
        signals.sort(key=lambda s: s.edge, reverse=True)
        return signals

    def _analyze_match(self, match: Match) -> List[ValueSignal]:
        signals = []

        # 1. Market consensus (все БК) — через Shin
        avg_odds = match.avg_odds
        market_probs = self.get_fair_probabilities(avg_odds, method="shin")

        # 2. Ensemble model prediction (Dixon-Coles + Elo + market)
        model_probs = {}
        model_count = 0
        if self.predictor:
            pred = self.predictor.predict(
                match.home_team, match.away_team, market_probs
            )
            if pred:
                model_probs = {k: v for k, v in pred.items()
                               if k in ("home", "draw", "away", "over", "under")}
                model_count = pred.get("model_count", 1)

        # 2.5 NLP Injury/News Adjustment
        if self.injury_scanner and "home" in model_probs:
            adj = self.injury_scanner.get_injury_adjustment(match.home_team, match.away_team)
            if adj:
                logger.info(f"Applying NLP Injury Adjustment: {adj}")
                for outcome in ["home", "draw", "away"]:
                    if outcome in model_probs:
                        model_probs[outcome] *= (1 + adj.get(outcome, 0))
                
                # Re-normalize
                total = sum(v for k, v in model_probs.items() if k in ["home", "draw", "away"])
                if total > 0:
                    for k in ["home", "draw", "away"]:
                        if k in model_probs: model_probs[k] /= total

        # Fallback: если нет ML-модели, используем Shin probabilities
        if not model_probs:
            model_probs = market_probs
            model_count = 1

        # 3. Сравниваем с лучшими кф
        for outcome_name, model_prob in model_probs.items():
            best_odds, best_bm = self._find_best_odds(match, outcome_name)
            if best_odds <= 1.0:
                continue

            # Edge
            edge = model_prob * best_odds - 1.0

            # Фильтры
            if edge < self.cfg.MIN_VALUE_EDGE or edge > self.cfg.MAX_VALUE_EDGE:
                continue
            if best_odds < self.cfg.MIN_ODDS or best_odds > self.cfg.MAX_ODDS:
                continue
            if len(match.bookmaker_odds) < self.cfg.MIN_BOOKMAKERS:
                continue

            # Проверка против «шарпового» БК (Pinnacle)
            sharp_check = self._check_sharp_bookmaker(
                match, outcome_name, model_prob
            )
            
            # Sharp Money Detection (from live_monitor.py)
            sharp_money = False
            if self.sharp_detector:
                # Convert Match.bookmaker_odds to format expected by SharpMoneyDetector
                odds_by_bk = {bo.bookmaker: bo.outcomes for bo in match.bookmaker_odds}
                sharp_res = self.sharp_detector.detect(match.id, odds_by_bk)
                sharp_money = sharp_res.get("is_sharp_money", False)

            # Confidence Level
            if model_count >= 3 and (edge >= self.cfg.MIN_CONFIRMED_EDGE or sharp_money):
                confidence = ConfidenceLevel.HIGH
            elif model_count >= 2 and edge >= self.cfg.MIN_VALUE_EDGE:
                confidence = ConfidenceLevel.MEDIUM
            else:
                confidence = ConfidenceLevel.LOW

            outcome_enum = self._map_outcome(outcome_name)
            if not outcome_enum:
                continue

            # Sharp Move Detection
            sharp_move = False
            # В реальном цикле здесь брались бы старые кф из Redis
            # if match.old_odds:
            #    moves = self.detect_line_movement(match.old_odds, match.avg_odds)
            #    sharp_move = any(m["is_sharp_move"] for m in moves if m["outcome"] == outcome_name)

            signal = ValueSignal(
                match=match,
                market=Market.H2H,
                outcome=outcome_enum,
                model_probability=model_prob,
                bookmaker_odds=best_odds,
                bookmaker_name=best_bm,
                edge=edge,
                confidence_level=confidence,
                model_count=model_count,
                sharp_agrees=sharp_check or sharp_move,
                status=SignalStatus.PENDING,
            )
            signals.append(signal)

        return signals

    def find_express_candidates(self, matches: List[Match]) -> List[ValueSignal]:
        """
        Special scan for express legs (Parlays).
        Focuses on High Probability (>40%) even with low/negative edge.
        """
        candidates = []
        for match in matches:
            # 1. Market consensus (probabilities)
            avg_odds = match.avg_odds
            market_probs = self.get_fair_probabilities(avg_odds, method="shin")
            
            # Use Model if available, else Market
            probs = market_probs
            if self.predictor:
                pred = self.predictor.predict(match.home_team, match.away_team, market_probs)
                if pred:
                    probs = {k: v for k, v in pred.items() if k in ["home", "draw", "away"]}

            for outcome, prob in probs.items():
                if prob < self.cfg.EXPRESS_MIN_LEG_PROB: # 0.40
                    continue
                
                best_odds, best_bm = self._find_best_odds(match, outcome)
                if best_odds <= 1.0:
                    continue

                edge = prob * best_odds - 1.0
                
                # Allow slightly negative edge for express legs (margin eating)
                # e.g. -5% acceptable if probability is high
                if edge < -0.05: 
                    continue
                    
                # Create signal
                outcome_enum = self._map_outcome(outcome)
                if outcome_enum:
                    candidates.append(ValueSignal(
                        match=match,
                        market=Market.H2H, 
                        outcome=outcome_enum,
                        model_probability=prob,
                        bookmaker_odds=best_odds,
                        bookmaker_name=best_bm,
                        edge=edge,
                        confidence_level=ConfidenceLevel.LOW 
                    ))
        
        candidates.sort(key=lambda s: s.model_probability, reverse=True)
        return candidates

    def _check_sharp_bookmaker(self, match: Match, outcome: str,
                                model_prob: float) -> bool:
        """
        Проверка: согласен ли Pinnacle (шарповый БК) с нашей оценкой?
        
        Если implied prob Pinnacle < model_prob → наша модель видит 
        value, который Pinnacle не видит (может быть edge, а может ошибка).
        """
        if not self.cfg.SHARP_BOOKMAKER:
            return False

        for bo in match.bookmaker_odds:
            if bo.bookmaker.lower() == self.cfg.SHARP_BOOKMAKER.lower():
                if outcome in bo.outcomes:
                    sharp_implied = 1.0 / bo.outcomes[outcome]
                    # Pinnacle «согласен» если его implied < наша P
                    return sharp_implied < model_prob * 1.02  # 2% tolerance
        return False  # Pinnacle не найден

    # ═══════════════════════════════════════════════════════
    #  CORRELATION-AWARE ЭКСПРЕССЫ
    # ═══════════════════════════════════════════════════════

    def build_express_bets(self, signals: List[ValueSignal],
                           num_legs: int = 3,
                           max_expresses: int = 5) -> List[ExpressBet]:
        """
        Экспрессы с учётом корреляции между ногами.
        
        Штрафы:
        - Каждая доп. нога: ×CORRELATION_DISCOUNT (0.95)
        - Ноги из одной лиги: ×SAME_LEAGUE_PENALTY (0.90)
        - Ноги в один день: ×SAME_DAY_PENALTY (0.97)
        
        Источник: исследования показали, что матчи одной лиги 
        и одного дня имеют скрытую корреляцию через погоду, 
        судей и турнирную ситуацию.
        """
        eligible = [
            s for s in signals
            if (s.edge >= self.cfg.EXPRESS_MIN_LEG_EDGE
                and s.model_probability >= self.cfg.EXPRESS_MIN_LEG_PROB
                and s.bookmaker_odds <= self.cfg.EXPRESS_MAX_LEG_ODDS
                and s.match is not None)
        ]

        if len(eligible) < num_legs:
            return []

        # Sort by probability for safer expresses
        eligible.sort(key=lambda s: s.model_probability, reverse=True)
        eligible = eligible[:50]  # Increased limit to finding more combos

        expresses = []

        for combo in combinations(eligible, num_legs):
            # Дубликат матча?
            match_ids = set()
            dup = False
            for s in combo:
                if s.match.id in match_ids:
                    dup = True
                    break
                match_ids.add(s.match.id)
            if dup:
                continue

            legs = [
                ExpressLeg(
                    signal=s,
                    odds=s.bookmaker_odds,
                    probability=s.model_probability,
                    edge=s.edge,
                )
                for s in combo
            ]

            # Рассчитываем корреляционный дисконт
            discount = self._correlation_discount(combo)

            # Генерация анализа AI
            analysis_lines = []
            analysis_lines.append("🤖 <b>Обоснование выбора:</b>")
            for leg in legs:
                reason = "✅ Баланс риска/прибыли"
                if leg.probability > 0.60:
                    reason = "🛡️ Высокая вероятность (Фаворит)"
                elif leg.edge > 0.30:
                    reason = "💎 Сверх-валуй (Value Bet)"
                elif leg.edge > 0.10:
                    reason = "📈 Хороший перевес над линией"
                elif leg.odds > 4.0:
                    reason = "🚀 Потенциальный апсет (Андердог)"
                
                team = leg.signal.match.home_team if leg.signal.outcome == BetOutcome.HOME else \
                       leg.signal.match.away_team if leg.signal.outcome == BetOutcome.AWAY else "Ничья"
                
                analysis_lines.append(f"- <b>{team}</b>: {reason} (P={leg.probability:.0%}, Edge={leg.edge:+.0%})")

            express = ExpressBet(
                legs=legs, 
                correlation_discount=discount,
                analysis="\n".join(analysis_lines)
            )

            # Фильтр по EV с учётом корреляции
            if express.adjusted_ev > 0 and \
               express.total_odds <= self.cfg.EXPRESS_MAX_TOTAL_ODDS:
                expresses.append(express)

        expresses.sort(key=lambda e: e.adjusted_ev, reverse=True)
        return expresses[:max_expresses]

    def _correlation_discount(self, signals) -> float:
        """
        Рассчитать дисконт за корреляцию между ногами экспресса.
        """
        discount = 1.0
        n = len(signals)

        # Штраф за количество ног
        for _ in range(n - 1):
            discount *= self.cfg.EXPRESS_CORRELATION_DISCOUNT

        # Штраф за одну лигу
        leagues = [s.match.league for s in signals if s.match]
        unique_leagues = set(leagues)
        if len(unique_leagues) < len(leagues):
            same_league_pairs = len(leagues) - len(unique_leagues)
            for _ in range(same_league_pairs):
                discount *= self.cfg.EXPRESS_SAME_LEAGUE_PENALTY

        # Штраф за один день
        dates = [s.match.commence_time.date() for s in signals if s.match]
        unique_dates = set(dates)
        if len(unique_dates) < len(dates):
            discount *= self.cfg.EXPRESS_SAME_DAY_PENALTY

        return round(discount, 4)

    # ═══════════════════════════════════════════════════════
    #  СИСТЕМЫ
    # ═══════════════════════════════════════════════════════

    def build_system_bets(self, signals: List[ValueSignal]) -> List[SystemBet]:
        """Формируем системы по всем конфигам из настроек"""
        systems = []
        for total, size in self.cfg.SYSTEM_CONFIGS:
            system = self._build_system(signals, total, size)
            if system:
                systems.append(system)
        return systems

    def _build_system(self, signals: List[ValueSignal],
                      total_legs: int, system_size: int) -> Optional[SystemBet]:
        eligible = [
            s for s in signals
            if (s.edge >= self.cfg.EXPRESS_MIN_LEG_EDGE
                and s.model_probability >= 0.55
                and s.match is not None)
        ]
        if len(eligible) < total_legs:
            return None

        selected = eligible[:total_legs]
        legs = [
            ExpressLeg(
                signal=s, odds=s.bookmaker_odds,
                probability=s.model_probability, edge=s.edge,
            )
            for s in selected
        ]

        return SystemBet(
            legs=legs, system_size=system_size, total_legs=total_legs,
        )

    # ═══════════════════════════════════════════════════════
    #  UTILITIES
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _find_best_odds(match: Match, outcome: str) -> Tuple[float, str]:
        best_odds, best_bm = 0.0, ""
        for bo in match.bookmaker_odds:
            if outcome in bo.outcomes and bo.outcomes[outcome] > best_odds:
                best_odds = bo.outcomes[outcome]
                best_bm = bo.bookmaker
        return best_odds, best_bm

    @staticmethod
    def _map_outcome(name: str) -> Optional[BetOutcome]:
        mapping = {
            "home": BetOutcome.HOME, "away": BetOutcome.AWAY,
            "draw": BetOutcome.DRAW, "over": BetOutcome.OVER,
            "under": BetOutcome.UNDER,
        }
        return mapping.get(name.lower())

    @staticmethod
    def detect_line_movement(old_odds: dict, new_odds: dict,
                              threshold: float = 0.08) -> List[dict]:
        movements = []
        for outcome in old_odds:
            if outcome in new_odds:
                old_val, new_val = old_odds[outcome], new_odds[outcome]
                if old_val > 0:
                    change = (new_val - old_val) / old_val
                    if abs(change) >= threshold:
                        movements.append({
                            "outcome": outcome,
                            "old_odds": old_val,
                            "new_odds": new_val,
                            "change_pct": round(change * 100, 1),
                            "direction": "DROP" if change < 0 else "RISE",
                            "is_sharp_move": change < -threshold,
                        })
        return movements
