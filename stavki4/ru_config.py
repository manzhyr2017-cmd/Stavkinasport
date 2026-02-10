"""
=============================================================================
 RUSSIAN BOOKMAKERS CONFIG
 
 Настройки специфичные для работы с русскими БК.
 Импортируется в основной settings.py
=============================================================================
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class FonbetConfig:
    """Конфигурация Фонбет"""
    # API endpoints
    BASE_URLS: List[str] = field(default_factory=lambda: [
        "https://line1.bk6.top",
        "https://line2.bk6.top",
        "https://line3.bk6.top",
    ])
    PREMATCH_ENDPOINT: str = "/line/currentLine/ru"
    LIVE_ENDPOINT: str = "/live/currentLine/ru"

    # Polling
    PREMATCH_INTERVAL: int = 120   # секунд
    LIVE_INTERVAL: int = 30        # секунд

    # Маржа по спортам (средняя на топ-лиги)
    MARGINS: Dict[str, float] = field(default_factory=lambda: {
        "football_top": 0.045,      # АПЛ, Бундеслига
        "football_mid": 0.055,      # Серия А, Ла Лига, Лига 1
        "football_low": 0.075,      # Низшие лиги
        "hockey_top": 0.055,        # КХЛ, НХЛ
        "hockey_low": 0.07,
        "basketball_top": 0.055,    # НБА, Евролига
        "basketball_low": 0.07,
        "tennis_top": 0.055,        # ТБШ, ATP/WTA
        "tennis_low": 0.07,
        "esports": 0.06,
        "live": 0.08,               # Лайв (все спорты)
    })


@dataclass
class ExpressConfig:
    """Конфигурация экспрессов под Фонбет"""
    # Страховка экспресса
    INSURANCE_MIN_LEGS: int = 6
    INSURANCE_MIN_ODDS: float = 1.60
    INSURANCE_ENABLED: bool = True

    # Лимиты ног
    MIN_LEG_ODDS: float = 1.60      # Совпадает со страховкой
    MAX_LEG_ODDS: float = 2.30
    MIN_LEG_PROB: float = 0.48
    MAX_TOTAL_ODDS: float = 30.0
    MAX_LEGS: int = 10

    # Предпочтительное кол-во ног
    PREFERRED_LEGS: List[int] = field(default_factory=lambda: [6, 7, 8])

    # Корреляционные дисконты
    DISCOUNT_PER_LEG: float = 0.95
    DISCOUNT_SAME_SPORT: float = 0.93
    DISCOUNT_SAME_LEAGUE: float = 0.88
    DISCOUNT_SAME_DAY: float = 0.97

    # Максимальный % банкролла на экспресс
    MAX_BANKROLL_PCT: float = 0.02   # 2%

    # Мультиспорт: идеальный микс
    IDEAL_SPORT_MIX: Dict[str, int] = field(default_factory=lambda: {
        "football": 2,
        "hockey": 1,
        "basketball": 1,
        "tennis": 1,
        "esports": 1,
    })


@dataclass
class RuBankrollConfig:
    """Банкролл для русского рынка (в рублях)"""
    INITIAL_BANKROLL: float = 10000.0   # 10,000₽
    CURRENCY: str = "RUB"

    # Kelly
    KELLY_FRACTION: float = 0.20
    MAX_SINGLE_BET_PCT: float = 0.04    # 4% от банкролла
    MAX_EXPRESS_BET_PCT: float = 0.02   # 2%

    # Stop-loss
    MAX_DAILY_LOSS_PCT: float = 0.08    # 8%
    MAX_WEEKLY_LOSS_PCT: float = 0.15   # 15%
    MAX_LOSING_STREAK: int = 7
    DRAWDOWN_PAUSE_PCT: float = 0.30    # 30% от пика


@dataclass
class RuConfig:
    """Объединённая конфигурация"""
    fonbet: FonbetConfig = field(default_factory=FonbetConfig)
    express: ExpressConfig = field(default_factory=ExpressConfig)
    bankroll: RuBankrollConfig = field(default_factory=RuBankrollConfig)

    # Какие БК активны
    ACTIVE_BOOKMAKERS: List[str] = field(default_factory=lambda: [
        "fonbet",        # Основная
        # "1xstavka",    # Раскомментировать когда подключим
        # "melbet",
    ])

    # Виды спорта
    ACTIVE_SPORTS: List[str] = field(default_factory=lambda: [
        "football", "hockey", "basketball",
        "tennis", "esports",
    ])

    # ODDSCORP API (платный агрегатор)
    ODDSCORP_API_KEY: str = ""
    ODDSCORP_ENABLED: bool = False

    # Telegram
    TELEGRAM_LANGUAGE: str = "ru"


# Глобальный инстанс
ru_config = RuConfig()
