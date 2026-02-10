"""
=============================================================================
 BETTING ASSISTANT V2 — BANKROLL MANAGEMENT
 
 Улучшения:
 1. Adaptive Kelly: fraction снижается после серии проигрышей
 2. Kelly для взаимоисключающих исходов (multi-outcome Kelly)
 3. Losing streak detection (7+ проигрышей = пауза)
 4. Drawdown-based position sizing
 5. Separate limits для singles / express / systems
 
 Источник: "Optimal sports betting strategies in practice"
 arxiv.org/pdf/2107.08827 — Fractional Kelly лучше на практике
=============================================================================
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List

from config.settings import betting_config
from core.models import ExpressBet, SystemBet, ValueSignal

logger = logging.getLogger(__name__)


@dataclass
class BetRecord:
    signal_id: str
    bet_type: str
    stake: float
    odds: float
    result: str = "pending"
    profit: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class BankrollManager:

    def __init__(self, initial_bankroll: float = None):
        self.bankroll = initial_bankroll or betting_config.INITIAL_BANKROLL
        self.initial_bankroll = self.bankroll
        self.peak_bankroll = self.bankroll  # Для drawdown
        self.cfg = betting_config

        self.bet_history: List[BetRecord] = []
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._daily_reset: datetime = datetime.utcnow().replace(
            hour=0, minute=0, second=0
        )
        self._losing_streak: int = 0
        self._is_stopped: bool = False
        self._stop_reason: str = ""

    # ═══════════════════════════════════════════════════════
    #  ADAPTIVE KELLY CRITERION
    # ═══════════════════════════════════════════════════════

    @property
    def adaptive_kelly_fraction(self) -> float:
        """
        Адаптивный Kelly fraction:
        - Базовый = KELLY_FRACTION (0.20)
        - Снижается при серии проигрышей
        - Снижается при drawdown > 10%
        
        Источник: адаптивный fractional Kelly из arxiv.org/pdf/2107.08827
        """
        base = self.cfg.KELLY_FRACTION

        # Снижение при losing streak
        if self._losing_streak >= 5:
            base *= 0.50  # Половина при 5+ проигрышах
        elif self._losing_streak >= 3:
            base *= 0.75  # 75% при 3-4 проигрышах

        # Снижение при drawdown
        drawdown = self.current_drawdown
        if drawdown > 0.15:
            base *= 0.50
        elif drawdown > 0.10:
            base *= 0.75

        return base

    @property
    def current_drawdown(self) -> float:
        """Текущий drawdown от пика"""
        if self.peak_bankroll <= 0:
            return 0
        return (self.peak_bankroll - self.bankroll) / self.peak_bankroll

    def kelly_single(self, signal: ValueSignal) -> float:
        """
        Kelly для одиночной ставки.
        
        f* = (b*p - q) / b  ×  adaptive_kelly_fraction
        
        Ограничения:
        - Макс. MAX_BET_PERCENT от банка
        - Мин. MIN_BET_AMOUNT
        """
        if self._is_stopped:
            return 0.0

        p = signal.model_probability
        b = signal.bookmaker_odds - 1.0
        q = 1.0 - p

        if b <= 0 or p <= 0:
            return 0.0

        f_star = (b * p - q) / b
        if f_star <= 0:
            return 0.0

        f = f_star * self.adaptive_kelly_fraction
        f = min(f, self.cfg.MAX_BET_PERCENT)

        stake = round(self.bankroll * f, 2)
        return stake if stake >= self.cfg.MIN_BET_AMOUNT else 0.0

    def kelly_express(self, express: ExpressBet) -> float:
        """
        Kelly для экспресса с учётом корреляции.
        
        f* = (total_odds × adjusted_prob - 1) / (total_odds - 1) 
             × adaptive_kelly × 0.5
        """
        if self._is_stopped:
            return 0.0

        total_odds = express.total_odds
        prob = express.adjusted_probability  # С дисконтом

        if total_odds <= 1.0 or prob <= 0:
            return 0.0

        b = total_odds - 1.0
        f_star = (b * prob - (1 - prob)) / b
        if f_star <= 0:
            return 0.0

        # Для экспрессов — ещё осторожнее
        f = f_star * self.adaptive_kelly_fraction * 0.5
        f = min(f, self.cfg.MAX_EXPRESS_BET_PERCENT)

        stake = round(self.bankroll * f, 2)
        return stake if stake >= self.cfg.MIN_BET_AMOUNT else 0.0

    def kelly_system(self, system: SystemBet) -> float:
        """Ставка на каждый экспресс в системе"""
        if self._is_stopped:
            return 0.0
        max_total = self.bankroll * self.cfg.MAX_SYSTEM_BET_PERCENT
        per_combo = max_total / system.num_combinations
        return round(max(per_combo, 0.5), 2)

    # ═══════════════════════════════════════════════════════
    #  STOP-LOSS + LOSING STREAK
    # ═══════════════════════════════════════════════════════

    def check_stop_conditions(self) -> bool:
        """
        Проверка всех условий остановки:
        1. Daily loss > 8%
        2. Weekly loss > 15%
        3. Losing streak >= 7
        4. Bankroll < 15% от начального
        5. Drawdown > 30% от пика
        """
        self._update_periods()

        # Daily
        if abs(min(self._daily_pnl, 0)) > self.bankroll * self.cfg.MAX_DAILY_LOSS_PERCENT:
            self._stop("Daily stop-loss triggered")
            return True

        # Weekly
        if abs(min(self._weekly_pnl, 0)) > self.bankroll * self.cfg.MAX_WEEKLY_LOSS_PERCENT:
            self._stop("Weekly stop-loss triggered")
            return True

        # Losing streak
        if self._losing_streak >= self.cfg.MAX_LOSING_STREAK:
            self._stop(f"Losing streak: {self._losing_streak} losses in a row")
            return True

        # Bankruptcy threshold
        if self.bankroll < self.initial_bankroll * self.cfg.BANKRUPTCY_THRESHOLD:
            self._stop(f"Bankroll critical: {self.bankroll:.2f}$")
            return True

        # Drawdown from peak
        if self.current_drawdown > 0.30:
            self._stop(f"Max drawdown: {self.current_drawdown:.1%} from peak")
            return True

        return False

    def _stop(self, reason: str):
        self._is_stopped = True
        self._stop_reason = reason
        logger.warning(f"⛔ BANKROLL STOPPED: {reason}")

    def reset_stop(self):
        self._is_stopped = False
        self._stop_reason = ""
        self._losing_streak = 0
        logger.info("✅ Bankroll stop reset")

    # ═══════════════════════════════════════════════════════
    #  TRACKING
    # ═══════════════════════════════════════════════════════

    def record_bet(self, signal_id: str, bet_type: str,
                   stake: float, odds: float) -> BetRecord:
        record = BetRecord(signal_id=signal_id, bet_type=bet_type,
                           stake=stake, odds=odds)
        self.bet_history.append(record)
        self.bankroll -= stake
        return record

    def settle_bet(self, signal_id: str, result: str):
        for record in reversed(self.bet_history):
            if record.signal_id == signal_id and record.result == "pending":
                record.result = result
                if result == "won":
                    profit = record.stake * record.odds - record.stake
                    record.profit = profit
                    self.bankroll += record.stake + profit
                    self._losing_streak = 0  # Reset streak
                elif result == "void":
                    record.profit = 0.0
                    self.bankroll += record.stake
                else:
                    record.profit = -record.stake
                    self._losing_streak += 1

                self._daily_pnl += record.profit
                self._weekly_pnl += record.profit

                # Update peak
                if self.bankroll > self.peak_bankroll:
                    self.peak_bankroll = self.bankroll

                self.check_stop_conditions()
                return
        logger.warning(f"Bet not found: {signal_id}")

    def _update_periods(self):
        now = datetime.utcnow()
        if now.date() > self._daily_reset.date():
            self._daily_pnl = 0.0
            self._daily_reset = now.replace(hour=0, minute=0, second=0)
        if now.weekday() == 0 and (now - self._daily_reset) > timedelta(hours=1):
            self._weekly_pnl = 0.0

    # ═══════════════════════════════════════════════════════
    #  STATISTICS
    # ═══════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        settled = [b for b in self.bet_history if b.result != "pending"]
        won = [b for b in settled if b.result == "won"]
        total_staked = sum(b.stake for b in settled)
        total_profit = sum(b.profit for b in settled)
        win_rate = len(won) / len(settled) if settled else 0
        roi = total_profit / total_staked if total_staked > 0 else 0

        return {
            "bankroll": round(self.bankroll, 2),
            "initial": self.initial_bankroll,
            "peak": round(self.peak_bankroll, 2),
            "drawdown": round(self.current_drawdown, 3),
            "total_bets": len(self.bet_history),
            "settled": len(settled),
            "won": len(won),
            "lost": len(settled) - len(won),
            "win_rate": round(win_rate, 3),
            "total_staked": round(total_staked, 2),
            "total_profit": round(total_profit, 2),
            "roi": round(roi, 4),
            "losing_streak": self._losing_streak,
            "kelly_fraction": round(self.adaptive_kelly_fraction, 3),
            "daily_pnl": round(self._daily_pnl, 2),
            "weekly_pnl": round(self._weekly_pnl, 2),
            "is_stopped": self._is_stopped,
            "stop_reason": self._stop_reason,
        }

    def stats_telegram(self) -> str:
        s = self.get_stats()
        status = "🔴 STOP" if s["is_stopped"] else "🟢 Active"
        dd = f"📉 Drawdown: {s['drawdown']:.1%}" if s['drawdown'] > 0.05 else ""
        streak_warn = (
            f"\n⚠️ Серия: {s['losing_streak']} проигрышей"
            if s['losing_streak'] >= 3 else ""
        )
        return (
            f"📊 <b>BANKROLL</b> {status}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>{s['bankroll']}$</b> (старт: {s['initial']}$)\n"
            f"📈 Пик: {s['peak']}$  {dd}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 Ставок: {s['total_bets']} "
            f"(✅{s['won']} ❌{s['lost']})\n"
            f"🎯 Win Rate: <b>{s['win_rate']:.1%}</b>\n"
            f"💎 ROI: <b>{s['roi']:.1%}</b>\n"
            f"📐 Kelly: {s['kelly_fraction']:.0%}{streak_warn}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Сегодня: {s['daily_pnl']:+.2f}$\n"
            f"📆 Неделя: {s['weekly_pnl']:+.2f}$\n"
        )
