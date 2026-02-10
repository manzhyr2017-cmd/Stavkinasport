"""
=============================================================================
 RUSSIAN BOOKMAKERS CONFIG — v2 (with insurance presets)
=============================================================================
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class InsurancePreset:
    """Пресет страховки экспресса"""
    name: str
    min_legs: int
    min_leg_odds: float
    min_total_odds: float = 0      # 0 = без ограничения
    no_asian: bool = True           # Запрет азиатских тоталов/фор
    no_returns: bool = True         # Запрет возвратов
    max_refund: float = 15000       # Макс. возврат в рублях


@dataclass
class FonbetConfig:
    """Конфигурация Фонбет"""
    BASE_URLS: List[str] = field(default_factory=lambda: [
        "https://line1.bk6.top",
        "https://line2.bk6.top",
        "https://line3.bk6.top",
        "https://line04.bk6.top",
        "https://line05.bk6.top",
    ])
    PREMATCH_ENDPOINT: str = "/line/currentLine/ru"
    LIVE_ENDPOINT: str = "/live/currentLine/ru"

    PREMATCH_INTERVAL: int = 120
    LIVE_INTERVAL: int = 30

    # Rate limiting
    MIN_DELAY_BETWEEN_REQUESTS: float = 1.5   # секунд
    MAX_DELAY_BETWEEN_REQUESTS: float = 3.5
    HEALTH_CHECK_INTERVAL: int = 600           # 10 минут

    MARGINS: Dict[str, float] = field(default_factory=lambda: {
        "football_top": 0.045,
        "football_mid": 0.055,
        "football_low": 0.075,
        "hockey_top": 0.055,
        "hockey_low": 0.07,
        "basketball_top": 0.055,
        "basketball_low": 0.07,
        "tennis_top": 0.055,
        "tennis_low": 0.07,
        "esports": 0.06,
        "live": 0.08,
    })

    # Пресеты страховки (условия могут меняться, поэтому конфигурируемые!)
    INSURANCE_PRESETS: Dict[str, InsurancePreset] = field(default_factory=lambda: {
        "fonbet_v1": InsurancePreset(
            name="Фонбет: 6+ ног, кф >= 1.60",
            min_legs=6, min_leg_odds=1.60, min_total_odds=0,
            max_refund=15000,
        ),
        "fonbet_v2": InsurancePreset(
            name="Фонбет: 5+ ног, кф >= 1.40, общий >= 10",
            min_legs=5, min_leg_odds=1.40, min_total_odds=10.0,
            max_refund=15000,
        ),
    })
    ACTIVE_INSURANCE_PRESET: str = "fonbet_v1"


@dataclass
class ExpressConfig:
    """Конфигурация экспрессов"""
    MIN_LEG_ODDS: float = 1.60
    MAX_LEG_ODDS: float = 2.30
    MIN_LEG_PROB: float = 0.48
    MAX_TOTAL_ODDS: float = 30.0
    MAX_LEGS: int = 10
    PREFERRED_LEGS: List[int] = field(default_factory=lambda: [6, 7, 8])

    DISCOUNT_PER_LEG: float = 0.95
    DISCOUNT_SAME_SPORT: float = 0.93
    DISCOUNT_SAME_LEAGUE: float = 0.88
    DISCOUNT_SAME_DAY: float = 0.97

    MAX_BANKROLL_PCT: float = 0.02

    # Стратегия "Противоход"
    HEDGE_ENABLED: bool = True
    HEDGE_MIN_GUARANTEED_ROI: float = 0.10   # 10%
    HEDGE_AUTO_NOTIFY: bool = True            # Уведомлять в Telegram

    # Cash-out
    CASHOUT_MONITOR_ENABLED: bool = True
    CASHOUT_SELL_THRESHOLD: float = 0.80      # 80% от потенциала → продать

    # Суперэкспресс
    SUPEREXPRESS_ENABLED: bool = True
    SUPEREXPRESS_MIN_CONFIDENCE: int = 9      # Мин. "high" прогнозов

    IDEAL_SPORT_MIX: Dict[str, int] = field(default_factory=lambda: {
        "football": 2, "hockey": 1, "basketball": 1,
        "tennis": 1, "esports": 1,
    })


@dataclass
class RuBankrollConfig:
    """Банкролл (рубли)"""
    INITIAL_BANKROLL: float = 10000.0
    CURRENCY: str = "RUB"

    KELLY_FRACTION: float = 0.20
    MAX_SINGLE_BET_PCT: float = 0.04
    MAX_EXPRESS_BET_PCT: float = 0.02

    MAX_DAILY_LOSS_PCT: float = 0.08
    MAX_WEEKLY_LOSS_PCT: float = 0.15
    MAX_LOSING_STREAK: int = 7
    DRAWDOWN_PAUSE_PCT: float = 0.30


@dataclass
class RuConfig:
    """Объединённая конфигурация"""
    fonbet: FonbetConfig = field(default_factory=FonbetConfig)
    express: ExpressConfig = field(default_factory=ExpressConfig)
    bankroll: RuBankrollConfig = field(default_factory=RuBankrollConfig)

    ACTIVE_BOOKMAKERS: List[str] = field(default_factory=lambda: ["fonbet"])
    ACTIVE_SPORTS: List[str] = field(default_factory=lambda: [
        "football", "hockey", "basketball", "tennis", "esports",
    ])

    ODDSCORP_API_KEY: str = ""
    ODDSCORP_ENABLED: bool = False
    TELEGRAM_LANGUAGE: str = "ru"

    def get_active_insurance(self) -> InsurancePreset:
        """Получить активный пресет страховки"""
        key = self.fonbet.ACTIVE_INSURANCE_PRESET
        return self.fonbet.INSURANCE_PRESETS.get(
            key, list(self.fonbet.INSURANCE_PRESETS.values())[0]
        )


ru_config = RuConfig()
