"""
=============================================================================
 BETTING ASSISTANT V2 — DOMAIN MODELS
=============================================================================
"""
from __future__ import annotations
import uuid
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class Market(str, Enum):
    H2H = "h2h"
    TOTALS = "totals"
    SPREADS = "spreads"

class BetOutcome(str, Enum):
    HOME = "home"
    AWAY = "away"
    DRAW = "draw"
    OVER = "over"
    UNDER = "under"

class SignalStatus(str, Enum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"
    EXPIRED = "expired"

class ConfidenceLevel(str, Enum):
    LOW = "⚪ Low"
    MEDIUM = "🟡 Medium"
    HIGH = "🟢 High"


@dataclass
class BookmakerOdds:
    bookmaker: str
    market: Market
    outcomes: dict  # {"home": 2.10, "draw": 3.40, "away": 3.60}
    last_update: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Match:
    id: str
    sport: str
    league: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmaker_odds: List[BookmakerOdds] = field(default_factory=list)

    @property
    def best_odds(self) -> dict:
        best = {}
        for bo in self.bookmaker_odds:
            for outcome, odds in bo.outcomes.items():
                if outcome not in best or odds > best[outcome]:
                    best[outcome] = odds
        return best

    @property
    def avg_odds(self) -> dict:
        sums, counts = {}, {}
        for bo in self.bookmaker_odds:
            for outcome, odds in bo.outcomes.items():
                sums[outcome] = sums.get(outcome, 0) + odds
                counts[outcome] = counts.get(outcome, 0) + 1
        return {k: sums[k] / counts[k] for k in sums}

    @property
    def overround(self) -> float:
        """Маржа букмекеров (сумма implied > 1)"""
        avg = self.avg_odds
        if not avg:
            return 0
        return sum(1.0 / v for v in avg.values()) - 1.0

    @property
    def num_bookmakers(self) -> int:
        return len(set(bo.bookmaker for bo in self.bookmaker_odds))


@dataclass
class ValueSignal:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    match: Optional[Match] = None
    market: Market = Market.H2H
    outcome: BetOutcome = BetOutcome.HOME
    model_probability: float = 0.0
    bookmaker_odds: float = 0.0
    bookmaker_name: str = ""
    edge: float = 0.0
    kelly_stake: float = 0.0
    stake_amount: float = 0.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
    model_count: int = 1         # Сколько моделей подтвердили
    sharp_agrees: bool = False   # Согласен ли Pinnacle
    status: SignalStatus = SignalStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_telegram_message(self) -> str:
        if not self.match:
            return "⚠️ No match data"
        m = self.match
        emoji_map = {"home": "🏠", "away": "✈️", "draw": "🤝",
                     "over": "⬆️", "under": "⬇️"}
        emoji = emoji_map.get(self.outcome.value, "⚽")
        sharp = "✅ Sharp" if self.sharp_agrees else "⚠️ No sharp"

        return (
            f"🎯 <b>VALUE BET</b> {self.confidence_level.value}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚽ <b>{m.home_team}</b> vs <b>{m.away_team}</b>\n"
            f"🏆 {m.league} | 🕐 {m.commence_time:%d.%m %H:%M}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji} <b>{self.outcome.value.upper()}</b> @ "
            f"<b>{self.bookmaker_odds:.2f}</b> ({self.bookmaker_name})\n"
            f"🧠 P модели: <b>{self.model_probability:.1%}</b> "
            f"({self.model_count} модел{'и' if self.model_count < 5 else 'ей'})\n"
            f"💎 Edge: <b>+{self.edge:.1%}</b>\n"
            f"📊 {sharp} | Маржа: {m.overround:.1%}\n"
            f"💰 Ставка: <b>{self.stake_amount:.2f}$</b> "
            f"({self.kelly_stake:.1%} банка)\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <code>#{self.id}</code>"
        )


@dataclass
class ExpressLeg:
    signal: ValueSignal
    odds: float
    probability: float
    edge: float


@dataclass
class ExpressBet:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    legs: List[ExpressLeg] = field(default_factory=list)
    correlation_discount: float = 1.0  # NEW: дисконт за корреляцию
    stake_amount: float = 0.0
    status: SignalStatus = SignalStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def total_odds(self) -> float:
        r = 1.0
        for leg in self.legs:
            r *= leg.odds
        return r

    @property
    def combined_probability(self) -> float:
        """Базовая P (предполагая независимость)"""
        r = 1.0
        for leg in self.legs:
            r *= leg.probability
        return r

    @property
    def adjusted_probability(self) -> float:
        """P с учётом корреляции (discount)"""
        return self.combined_probability * self.correlation_discount

    @property
    def expected_value(self) -> float:
        return self.combined_probability * self.total_odds - 1

    @property
    def adjusted_ev(self) -> float:
        """EV с учётом корреляции"""
        return self.adjusted_probability * self.total_odds - 1

    @property
    def potential_win(self) -> float:
        return self.stake_amount * self.total_odds

    def to_telegram_message(self) -> str:
        legs_text = ""
        for i, leg in enumerate(self.legs, 1):
            m = leg.signal.match
            if m:
                legs_text += (
                    f"  {i}. {m.home_team} vs {m.away_team}\n"
                    f"     → {leg.signal.outcome.value.upper()} "
                    f"@ {leg.odds:.2f} (P:{leg.probability:.0%}, "
                    f"edge:{leg.edge:+.1%})\n"
                )

        corr_note = ""
        if self.correlation_discount < 1.0:
            corr_note = f"\n⚠️ Корр. дисконт: {self.correlation_discount:.0%}"

        return (
            f"🔥 <b>ЭКСПРЕСС ({len(self.legs)} событий)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{legs_text}"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Общий кф: <b>{self.total_odds:.2f}</b>\n"
            f"🧠 P: <b>{self.combined_probability:.1%}</b>"
            f"{corr_note}\n"
            f"💎 EV: <b>{self.adjusted_ev:+.1%}</b>\n"
            f"💰 Ставка: <b>{self.stake_amount:.2f}$</b>\n"
            f"🎯 Выигрыш: <b>{self.potential_win:.2f}$</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <code>#{self.id}</code>"
        )


@dataclass
class SystemBet:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    legs: List[ExpressLeg] = field(default_factory=list)
    system_size: int = 3
    total_legs: int = 4
    stake_per_combo: float = 0.0
    status: SignalStatus = SignalStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def num_combinations(self) -> int:
        return math.comb(self.total_legs, self.system_size)

    @property
    def total_stake(self) -> float:
        return self.stake_per_combo * self.num_combinations

    @property
    def avg_leg_prob(self) -> float:
        if not self.legs:
            return 0
        return sum(l.probability for l in self.legs) / len(self.legs)

    @property
    def expected_wins(self) -> float:
        """Ожидаемое число угаданных ног"""
        return sum(l.probability for l in self.legs)

    def to_telegram_message(self) -> str:
        legs_text = ""
        for i, leg in enumerate(self.legs, 1):
            m = leg.signal.match
            if m:
                legs_text += (
                    f"  {i}. {m.home_team} vs {m.away_team}\n"
                    f"     → {leg.signal.outcome.value.upper()} "
                    f"@ {leg.odds:.2f} (P:{leg.probability:.0%})\n"
                )

        return (
            f"🎰 <b>СИСТЕМА {self.system_size}/{self.total_legs}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{legs_text}"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Комбинаций: <b>{self.num_combinations}</b>\n"
            f"🧠 Средняя P ноги: <b>{self.avg_leg_prob:.0%}</b>\n"
            f"🎯 Ожидание: <b>{self.expected_wins:.1f}</b> из "
            f"{self.total_legs} угаданных\n"
            f"💰 <b>{self.stake_per_combo:.2f}$</b> × "
            f"{self.num_combinations} = "
            f"<b>{self.total_stake:.2f}$</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <code>#{self.id}</code>"
        )
