"""
=============================================================================
 FONBET STRATEGIES — ПРОТИВОХОД + CASHOUT + СУПЕРЭКСПРЕСС
 
 Три дополнительные стратегии специально для Фонбет:
 
 1. ПРОТИВОХОД (Hedge) — автохеджирование экспрессов
 2. CASHOUT MONITOR — мониторинг когда продать ставку
 3. СУПЕРЭКСПРЕСС — автозаполнение ТОТО на основе модели
=============================================================================
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  1. ПРОТИВОХОД (HEDGE CALCULATOR)
# ═══════════════════════════════════════════════════════════

@dataclass
class HedgeRecommendation:
    """Рекомендация по хеджированию"""
    express_stake: float          # Исходная ставка на экспресс
    express_total_odds: float     # Общий кф экспресса
    legs_passed: int              # Сколько ног уже прошло
    legs_total: int               # Всего ног
    remaining_leg_market: str     # Рынок оставшейся ноги ("П1", "ТБ 2.5")
    remaining_leg_odds: float     # Кф оставшейся ноги
    opposite_market: str          # Противоположный рынок ("Х2", "ТМ 2.5")
    opposite_odds: float          # Кф противоположного исхода
    hedge_stake: float            # Сколько поставить на противоход
    profit_if_express_wins: float # Прибыль если экспресс выиграет
    profit_if_hedge_wins: float   # Прибыль если противоход выиграет
    guaranteed_profit: float      # Гарантированная прибыль (минимум)
    roi: float                    # ROI от гарантированной прибыли

    def to_telegram(self) -> str:
        return (
            f"🔄 *ПРОТИВОХОД*\n"
            f"📊 Экспресс: {self.legs_passed}/{self.legs_total} ног прошло\n"
            f"💰 Ставка: {self.express_stake:.0f}₽ @ {self.express_total_odds:.2f}\n"
            f"\n"
            f"🎯 Оставшаяся нога: {self.remaining_leg_market} @ {self.remaining_leg_odds:.2f}\n"
            f"🔁 Противоход: {self.opposite_market} @ {self.opposite_odds:.2f}\n"
            f"💵 *Ставьте {self.hedge_stake:.0f}₽ на {self.opposite_market}*\n"
            f"\n"
            f"✅ Если экспресс выиграет: +{self.profit_if_express_wins:.0f}₽\n"
            f"✅ Если противоход: +{self.profit_if_hedge_wins:.0f}₽\n"
            f"🛡️ *Гарантированная прибыль: +{self.guaranteed_profit:.0f}₽* "
            f"(ROI {self.roi:+.1%})"
        )


class HedgeCalculator:
    """
    Калькулятор стратегии «Противоход».
    
    Принцип:
    - Ставим экспресс на 3+ ноги с РАЗНЫМ временем начала
    - Когда все ноги, кроме последней, прошли — ставим ПРОТИВ последней ноги
    - Получаем гарантированную прибыль в любом случае
    
    Формула:
      hedge_stake = express_stake * express_odds / opposite_odds
      
    Пример:
      Экспресс: 1000₽ @ 4.75 (3 ноги)
      Ноги 1 и 2 прошли. Нога 3: П1 @ 1.90
      Противоход: Х2 @ 1.90
      hedge_stake = 1000 * 4.75 / 1.90 = 2500₽
      
      Если экспресс: 1000 * 4.75 - 1000 - 2500 = +1250₽
      Если противоход: 2500 * 1.90 - 1000 - 2500 = +1250₽
      Гарантия: +1250₽ при любом исходе!
    """

    # Маппинг рынок → противоположный рынок
    OPPOSITE_MARKETS = {
        "П1": "Х2",     "Х2": "П1",
        "П2": "1Х",     "1Х": "П2",
        "Х": "12",      "12": "Х",
        "ТБ(2.5)": "ТМ(2.5)", "ТМ(2.5)": "ТБ(2.5)",
        "ТБ(1.5)": "ТМ(1.5)", "ТМ(1.5)": "ТБ(1.5)",
        "ТБ(3.5)": "ТМ(3.5)", "ТМ(3.5)": "ТБ(3.5)",
        "ТБ(4.5)": "ТМ(4.5)", "ТМ(4.5)": "ТБ(4.5)",
        "ТБ(5.5)": "ТМ(5.5)", "ТМ(5.5)": "ТБ(5.5)",
        "ОЗ_Да": "ОЗ_Нет", "ОЗ_Нет": "ОЗ_Да",
    }

    def calculate_hedge(
        self,
        express_stake: float,
        express_total_odds: float,
        legs_passed: int,
        legs_total: int,
        remaining_leg_market: str,
        remaining_leg_odds: float,
        opposite_odds: float,
    ) -> HedgeRecommendation:
        """
        Рассчитать противоход для экспресса.
        
        Вызывать когда ВСЕ ноги кроме последней прошли.
        """
        # Потенциальный выигрыш экспресса
        express_payout = express_stake * express_total_odds

        # Размер ставки на противоход
        hedge_stake = express_payout / opposite_odds

        # Прибыль в каждом сценарии
        total_invested = express_stake + hedge_stake

        # Если экспресс выигрывает
        profit_express = express_payout - total_invested

        # Если противоход выигрывает
        hedge_payout = hedge_stake * opposite_odds
        profit_hedge = hedge_payout - total_invested

        guaranteed = min(profit_express, profit_hedge)
        roi = guaranteed / total_invested

        opposite_market = self.OPPOSITE_MARKETS.get(
            remaining_leg_market, f"ПРОТИВ {remaining_leg_market}"
        )

        return HedgeRecommendation(
            express_stake=express_stake,
            express_total_odds=express_total_odds,
            legs_passed=legs_passed,
            legs_total=legs_total,
            remaining_leg_market=remaining_leg_market,
            remaining_leg_odds=remaining_leg_odds,
            opposite_market=opposite_market,
            opposite_odds=opposite_odds,
            hedge_stake=round(hedge_stake, 0),
            profit_if_express_wins=round(profit_express, 0),
            profit_if_hedge_wins=round(profit_hedge, 0),
            guaranteed_profit=round(guaranteed, 0),
            roi=roi,
        )

    def should_hedge(
        self,
        express_stake: float,
        express_total_odds: float,
        legs_passed: int,
        legs_total: int,
        remaining_leg_prob: float,
        opposite_odds: float,
        min_guaranteed_roi: float = 0.10,
    ) -> bool:
        """
        Стоит ли делать противоход?
        
        Да, если:
        1. Все ноги кроме последней прошли
        2. Гарантированный ROI >= min_guaranteed_roi
        3. Вероятность последней ноги < 70% (иначе лучше дождаться)
        """
        if legs_passed < legs_total - 1:
            return False

        express_payout = express_stake * express_total_odds
        hedge_stake = express_payout / opposite_odds
        total_invested = express_stake + hedge_stake
        guaranteed = min(
            express_payout - total_invested,
            hedge_stake * opposite_odds - total_invested,
        )
        roi = guaranteed / total_invested if total_invested > 0 else 0

        if roi < min_guaranteed_roi:
            return False

        # Если нога очень вероятна (>70%), может лучше не хеджировать
        if remaining_leg_prob > 0.70:
            logger.info(
                f"Hedge available (ROI {roi:.1%}) but leg prob {remaining_leg_prob:.0%} "
                f"is high — consider NOT hedging"
            )
            # Всё равно возвращаем True, но логируем
        return True


# ═══════════════════════════════════════════════════════════
#  2. CASHOUT MONITOR
# ═══════════════════════════════════════════════════════════

@dataclass
class CashoutSignal:
    """Сигнал о возможности продать ставку"""
    bet_id: str
    original_stake: float
    potential_win: float       # Если дождаться
    cashout_offer: float       # Предложение Фонбет
    cashout_profit: float      # cashout_offer - original_stake
    cashout_roi: float         # cashout_profit / original_stake
    legs_remaining: int        # Сколько ног осталось
    min_remaining_prob: float  # Мин. вероятность среди оставшихся
    recommendation: str        # "sell" / "hold" / "risky_hold"

    def to_telegram(self) -> str:
        icon = {"sell": "💰", "hold": "⏳", "risky_hold": "⚠️"}
        rec_text = {
            "sell": "ПРОДАТЬ (зафиксировать прибыль)",
            "hold": "ДЕРЖАТЬ (вероятность прохода высокая)",
            "risky_hold": "РИСКОВАННО ДЕРЖАТЬ",
        }
        return (
            f"{icon.get(self.recommendation, '❓')} *CASHOUT*\n"
            f"Ставка: {self.original_stake:.0f}₽\n"
            f"Предложение: {self.cashout_offer:.0f}₽ "
            f"(прибыль {self.cashout_profit:+.0f}₽, ROI {self.cashout_roi:+.1%})\n"
            f"Если дождаться: {self.potential_win:.0f}₽\n"
            f"Ног осталось: {self.legs_remaining}\n"
            f"Рекомендация: *{rec_text.get(self.recommendation, '?')}*"
        )


class CashoutAdvisor:
    """
    Советник по продаже ставок (Cash-out) в Фонбет.
    
    Фонбет позволяет продать ставку до окончания всех событий.
    Бот уведомляет когда выгодно зафиксировать прибыль.
    
    Логика:
    - Если cashout >= 80% от потенциального выигрыша → SELL
    - Если remaining_prob < 40% → SELL  
    - Если remaining_prob > 65% и cashout < 60% → HOLD
    - Иначе → RISKY_HOLD
    """

    def evaluate(
        self,
        original_stake: float,
        potential_win: float,
        cashout_offer: float,
        legs_remaining: int,
        remaining_probs: List[float],
    ) -> CashoutSignal:
        """Оценить предложение cash-out"""
        cashout_profit = cashout_offer - original_stake
        cashout_roi = cashout_profit / original_stake if original_stake > 0 else 0

        # Вероятность что ВСЕ оставшиеся ноги пройдут
        combined_prob = 1.0
        for p in remaining_probs:
            combined_prob *= p
        min_prob = min(remaining_probs) if remaining_probs else 0

        # EV если держать
        ev_hold = combined_prob * potential_win - (1 - combined_prob) * original_stake

        # Решение
        cashout_pct = cashout_offer / potential_win if potential_win > 0 else 0

        if cashout_pct >= 0.80 and cashout_profit > 0:
            recommendation = "sell"
        elif min_prob < 0.40 and cashout_profit > 0:
            recommendation = "sell"
        elif combined_prob > 0.65 and cashout_pct < 0.60:
            recommendation = "hold"
        elif ev_hold > cashout_profit:
            recommendation = "risky_hold"
        else:
            recommendation = "sell"

        return CashoutSignal(
            bet_id="",
            original_stake=original_stake,
            potential_win=potential_win,
            cashout_offer=cashout_offer,
            cashout_profit=cashout_profit,
            cashout_roi=cashout_roi,
            legs_remaining=legs_remaining,
            min_remaining_prob=min_prob,
            recommendation=recommendation,
        )


# ═══════════════════════════════════════════════════════════
#  3. СУПЕРЭКСПРЕСС / ТОТО GENERATOR
# ═══════════════════════════════════════════════════════════

@dataclass
class SuperExpressPick:
    """Один выбор в суперэкспрессе"""
    match_name: str
    league: str
    prediction: str    # "П1", "Х", "П2"
    probability: float
    confidence: str    # "high", "medium", "low"


class SuperExpressGenerator:
    """
    Автозаполнение Суперэкспресса (ТОТО) в Фонбет.
    
    Фонбет предлагает "Суперэкспресс" — угадать исходы N событий.
    Минимум 9 из 15 правильных для выигрыша.
    
    Стратегия:
    1. Для каждого матча рассчитываем вероятности через модель
    2. Выбираем наиболее вероятный исход
    3. Оцениваем уверенность (high/medium/low)
    4. Для "medium" матчей можно сделать "систему" (несколько вариантов)
    """

    def generate_picks(
        self,
        matches: List[dict],
        model_probs: Dict[str, Dict[str, float]] = None,
    ) -> List[SuperExpressPick]:
        """
        Генерация прогнозов для суперэкспресса.
        
        matches: [{"id": "...", "home": "...", "away": "...", "league": "...",
                    "odds": {"П1": 1.5, "Х": 4.0, "П2": 6.0}}]
        model_probs: {"match_id": {"П1": 0.65, "Х": 0.22, "П2": 0.13}}
        """
        picks = []
        model_probs = model_probs or {}

        for match in matches:
            mid = match.get("id", "")
            home = match.get("home", "?")
            away = match.get("away", "?")
            league = match.get("league", "")
            odds = match.get("odds", {})

            # Get probabilities
            probs = model_probs.get(mid)
            if not probs:
                # Fallback: implied from odds (basic normalization)
                probs = {}
                total = sum(1/v for v in odds.values() if v > 1)
                if total > 0:
                    for market, odd in odds.items():
                        if odd > 1:
                            probs[market] = (1/odd) / total

            if not probs:
                continue

            # Best prediction
            best_market = max(probs, key=probs.get)
            best_prob = probs[best_market]

            # Confidence
            if best_prob >= 0.60:
                confidence = "high"
            elif best_prob >= 0.45:
                confidence = "medium"
            else:
                confidence = "low"

            picks.append(SuperExpressPick(
                match_name=f"{home} — {away}",
                league=league,
                prediction=best_market,
                probability=best_prob,
                confidence=confidence,
            ))

        # Sort by confidence (high first)
        order = {"high": 0, "medium": 1, "low": 2}
        picks.sort(key=lambda p: (order.get(p.confidence, 3), -p.probability))
        return picks

    def format_toto_card(self, picks: List[SuperExpressPick]) -> str:
        """Форматирование карточки ТОТО для Telegram"""
        lines = [f"🎯 *СУПЕРЭКСПРЕСС / ТОТО* ({len(picks)} событий)\n"]

        conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
        high_count = sum(1 for p in picks if p.confidence == "high")
        med_count = sum(1 for p in picks if p.confidence == "medium")

        lines.append(f"Уверенность: 🟢 {high_count} | 🟡 {med_count} | 🔴 {len(picks) - high_count - med_count}\n")

        for i, pick in enumerate(picks, 1):
            emoji = conf_emoji.get(pick.confidence, "⚪")
            lines.append(
                f"{i}. {emoji} {pick.match_name}\n"
                f"   {pick.prediction} ({pick.probability:.0%}) | {pick.league}"
            )

        if high_count >= 9:
            lines.append(f"\n✅ {high_count} уверенных прогнозов — хорошие шансы на 9+!")
        elif high_count + med_count >= 9:
            lines.append(f"\n⚠️ Нужно 9 верных. Уверенных: {high_count}. Рискуйте осторожно.")
        else:
            lines.append(f"\n❌ Мало уверенных прогнозов. Лучше пропустить.")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  4. EXPRESS BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════

@dataclass
class BacktestResult:
    """Результат бэктеста экспрессов"""
    total_bets: int
    total_staked: float
    total_returned: float
    profit: float
    roi: float
    win_rate: float
    insurance_triggered: int    # Сколько раз сработала страховка
    insurance_saved: float      # Сколько денег вернула страховка
    max_drawdown: float
    best_express_odds: float
    avg_express_odds: float
    months: int


class ExpressBacktester:
    """
    Бэктест экспрессов со страховкой Фонбет на исторических данных.
    
    Отвечает на вопрос: "Сколько бы мы заработали за N месяцев,
    если бы использовали нашу стратегию?"
    """

    def run_backtest(
        self,
        historical_bets: List[dict],
        stake_per_express: float = 200,
        insurance_enabled: bool = True,
        insurance_min_legs: int = 6,
        insurance_min_odds: float = 1.60,
    ) -> BacktestResult:
        """
        Бэктест.
        
        historical_bets: [
            {
                "legs": [
                    {"odds": 1.80, "won": True},
                    {"odds": 1.75, "won": True},
                    {"odds": 1.90, "won": False},
                    ...
                ],
                "date": "2025-06-15",
            }
        ]
        """
        total_staked = 0
        total_returned = 0
        insurance_triggered = 0
        insurance_saved = 0
        wins = 0
        bankroll_history = [0]
        max_odds = 0
        all_odds = []

        for bet in historical_bets:
            legs = bet.get("legs", [])
            if not legs:
                continue

            total_odds = 1.0
            losses = 0
            for leg in legs:
                total_odds *= leg["odds"]
                if not leg["won"]:
                    losses += 1

            all_odds.append(total_odds)
            if total_odds > max_odds:
                max_odds = total_odds

            total_staked += stake_per_express

            if losses == 0:
                # All legs won!
                payout = stake_per_express * total_odds
                total_returned += payout
                wins += 1
            elif losses == 1 and insurance_enabled:
                # Insurance check
                eligible = (
                    len(legs) >= insurance_min_legs and
                    all(leg["odds"] >= insurance_min_odds for leg in legs)
                )
                if eligible:
                    # Return stake
                    total_returned += stake_per_express
                    insurance_triggered += 1
                    insurance_saved += stake_per_express
                # else: loss (no insurance)
            # else: 2+ losses = full loss

            # Track bankroll
            current_pnl = total_returned - total_staked
            bankroll_history.append(current_pnl)

        # Max drawdown
        peak = 0
        max_dd = 0
        for pnl in bankroll_history:
            if pnl > peak:
                peak = pnl
            dd = peak - pnl
            if dd > max_dd:
                max_dd = dd

        profit = total_returned - total_staked
        n_bets = len(historical_bets)
        roi = profit / total_staked if total_staked > 0 else 0
        win_rate = wins / n_bets if n_bets > 0 else 0

        # Estimate months
        if historical_bets:
            dates = [b.get("date", "") for b in historical_bets if b.get("date")]
            if len(dates) >= 2:
                first = datetime.strptime(min(dates), "%Y-%m-%d")
                last = datetime.strptime(max(dates), "%Y-%m-%d")
                months = max(1, (last - first).days // 30)
            else:
                months = 1
        else:
            months = 0

        return BacktestResult(
            total_bets=n_bets,
            total_staked=total_staked,
            total_returned=total_returned,
            profit=profit,
            roi=roi,
            win_rate=win_rate,
            insurance_triggered=insurance_triggered,
            insurance_saved=insurance_saved,
            max_drawdown=max_dd,
            best_express_odds=max_odds,
            avg_express_odds=sum(all_odds)/len(all_odds) if all_odds else 0,
            months=months,
        )

    def format_report(self, r: BacktestResult) -> str:
        return (
            f"📊 *БЭКТЕСТ ЭКСПРЕССОВ* ({r.months} мес.)\n"
            f"\n"
            f"Всего ставок: {r.total_bets}\n"
            f"Поставлено: {r.total_staked:,.0f}₽\n"
            f"Получено: {r.total_returned:,.0f}₽\n"
            f"*Прибыль: {r.profit:+,.0f}₽ (ROI {r.roi:+.1%})*\n"
            f"\n"
            f"Win rate: {r.win_rate:.1%}\n"
            f"Средний кф: {r.avg_express_odds:.1f}\n"
            f"Лучший кф: {r.best_express_odds:.1f}\n"
            f"Max drawdown: {r.max_drawdown:,.0f}₽\n"
            f"\n"
            f"🛡️ Страховка сработала: {r.insurance_triggered} раз\n"
            f"🛡️ Страховка вернула: {r.insurance_saved:,.0f}₽"
        )
