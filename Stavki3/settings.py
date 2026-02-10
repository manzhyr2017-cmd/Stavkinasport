"""
=============================================================================
 BETTING ASSISTANT V2 — CONFIGURATION
 Полностью переработанная конфигурация с Dixon-Coles, Elo, калибровкой
=============================================================================
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()


# ═══════════════════════════════════════════════════════════
#  API KEYS
# ═══════════════════════════════════════════════════════════
@dataclass
class APIConfig:
    # The Odds API (500 req/мес бесплатно)
    ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
    ODDS_API_BASE: str = "https://api.the-odds-api.com/v4"
    # Football-data.org (10 req/мин бесплатно) — результаты матчей
    FOOTBALL_DATA_KEY: str = os.getenv("FOOTBALL_DATA_KEY", "")
    FOOTBALL_DATA_BASE: str = "https://api.football-data.org/v4"
    # API-Football via RapidAPI — xG, статистика
    RAPID_API_KEY: str = os.getenv("RAPID_API_KEY", "")
    API_FOOTBALL_HOST: str = "v3.football.api-sports.io"


# ═══════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════
@dataclass
class TelegramConfig:
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ADMIN_IDS: List[int] = field(default_factory=lambda: [
        int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x
    ])
    CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")
    MAX_SIGNALS_PER_HOUR: int = 10
    SEND_DELAY_SECONDS: float = 1.5  # Пауза между сообщениями


# ═══════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════
@dataclass
class DatabaseConfig:
    POSTGRES_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://user:password@localhost:5432/betting_db"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_ODDS_TTL: int = 300       # Кеш кф живёт 5 минут
    REDIS_MATCH_TTL: int = 3600     # Кеш матча — 1 час


# ═══════════════════════════════════════════════════════════
#  МОДЕЛЬ ПРЕДСКАЗАНИЙ (Dixon-Coles + Elo + CatBoost)
# ═══════════════════════════════════════════════════════════
@dataclass
class ModelConfig:
    # --- Dixon-Coles Poisson Model ---
    DIXON_COLES_RHO_BOUNDS: tuple = (-0.5, 0.5)     # Границы rho (корреляция)
    DIXON_COLES_TIME_DECAY: float = 0.0019           # xi — экспоненциальный decay
    DIXON_COLES_MAX_GOALS: int = 7                   # Максимум голов для матрицы
    DIXON_COLES_SEASONS: int = 3                     # Сколько сезонов для обучения

    # --- Elo Rating ---
    ELO_INITIAL: float = 1500.0
    ELO_K_FACTOR: float = 32.0       # Скорость обновления рейтинга
    ELO_HOME_ADVANTAGE: float = 65.0  # Бонус домашней команде (в Elo-пунктах)
    ELO_MARGIN_MULTIPLIER: bool = True  # Учитывать разницу голов

    # --- CatBoost (если есть обученная модель) ---
    CATBOOST_MODEL_PATH: str = "models/catboost_value.cbm"
    USE_CATBOOST: bool = False  # False = только Dixon-Coles + Elo

    # --- Probability Calibration ---
    # Источник: arxiv.org/pdf/2303.06021 — калибровка важнее accuracy!
    CALIBRATION_METHOD: str = "isotonic"  # "isotonic" или "platt" (sigmoid)
    CALIBRATION_MIN_SAMPLES: int = 200    # Мин. сэмплов для isotonic
    BRIER_SCORE_THRESHOLD: float = 0.25   # Макс. допустимый Brier score

    # --- Ensemble Weights (микс моделей) ---
    # Веса: Dixon-Coles, Elo, Pinnacle-implied, CatBoost
    ENSEMBLE_WEIGHTS: Dict[str, float] = field(default_factory=lambda: {
        "dixon_coles": 0.35,
        "elo": 0.15,
        "market_consensus": 0.40,  # Средние кф от 10+ БК = "мудрость толпы"
        "catboost": 0.10,
    })

    RETRAIN_INTERVAL_DAYS: int = 7
    MIN_TRAINING_MATCHES: int = 500


# ═══════════════════════════════════════════════════════════
#  СТРАТЕГИЯ СТАВОК
# ═══════════════════════════════════════════════════════════
@dataclass
class BettingConfig:
    # --- Value Betting (одиночные) ---
    MIN_VALUE_EDGE: float = 0.03         # 3% edge — мин. для сигнала
    MIN_CONFIRMED_EDGE: float = 0.05     # 5% edge — подтверждённый (2+ модели)
    MAX_VALUE_EDGE: float = 0.25         # >25% — подозрительно
    MIN_ODDS: float = 1.35
    MAX_ODDS: float = 5.50

    # --- Фильтры качества ---
    MIN_BOOKMAKERS: int = 5       # Мин. БК, подтвердивших линию
    MIN_MODEL_AGREEMENT: int = 2  # Мин. моделей, подтвердивших edge
    SHARP_BOOKMAKER: str = "Pinnacle"  # "Шарп" БК (самые точные линии)

    # --- Экспресс (аккумулятор) ---
    EXPRESS_MIN_EVENTS: int = 2
    EXPRESS_MAX_EVENTS: int = 6    # Сокращено с 8 — меньше ног = выше вероятность
    EXPRESS_MIN_LEG_PROB: float = 0.52   # Мин. P ноги (а не просто edge)
    EXPRESS_MAX_LEG_ODDS: float = 2.20   # Макс. кф ноги (низкие = безопаснее)
    EXPRESS_MIN_LEG_EDGE: float = 0.03
    EXPRESS_MAX_TOTAL_ODDS: float = 15.0  # Макс. общий кф экспресса

    # --- Корреляция в экспрессах ---
    # Источник: исследования показывают, что матчи одной лиги/дня
    # НЕ полностью независимы (погода, судья, турнирная ситуация)
    EXPRESS_CORRELATION_DISCOUNT: float = 0.95  # Дисконт за каждую доп. ногу
    EXPRESS_SAME_LEAGUE_PENALTY: float = 0.90   # Штраф за 2 ноги из одной лиги
    EXPRESS_SAME_DAY_PENALTY: float = 0.97      # Штраф за матчи в тот же день

    # --- Система ---
    SYSTEM_CONFIGS: List[tuple] = field(default_factory=lambda: [
        (3, 2),  # 2 из 3
        (4, 3),  # 3 из 4
        (5, 3),  # 3 из 5
        (5, 4),  # 4 из 5
    ])

    # --- Bankroll Management ---
    INITIAL_BANKROLL: float = float(os.getenv("INITIAL_BANKROLL", "1000"))
    KELLY_FRACTION: float = 0.20      # 1/5 Келли — ещё консервативнее
    MAX_BET_PERCENT: float = 0.04     # Макс. 4% банка на одиночную
    MAX_EXPRESS_BET_PERCENT: float = 0.02  # Макс. 2% на экспресс
    MAX_SYSTEM_BET_PERCENT: float = 0.03   # Макс. 3% на систему
    MIN_BET_AMOUNT: float = 1.0

    # --- Stop-Loss ---
    MAX_DAILY_LOSS_PERCENT: float = 0.08     # 8% банка
    MAX_WEEKLY_LOSS_PERCENT: float = 0.15    # 15% банка
    MAX_LOSING_STREAK: int = 7               # 7 проигрышей подряд = пауза
    BANKRUPTCY_THRESHOLD: float = 0.15       # <15% начального = стоп

    # --- Мониторинг ---
    ODDS_POLL_INTERVAL: int = 120       # 2 мин. (экономия API запросов)
    LINE_DROP_THRESHOLD: float = 0.08   # 8% падение кф = sharp money
    LINE_DROP_CONFIRM_TIME: int = 60    # Подтверждение через 60 сек

    # --- Спорты и Лиги ---
    SPORTS: List[str] = field(default_factory=lambda: [
        "soccer_epl",
        "soccer_germany_bundesliga",
        "soccer_spain_la_liga",
        "soccer_italy_serie_a",
        "soccer_france_ligue_one",
        "soccer_uefa_champs_league",
        "soccer_uefa_europa_league",
    ])
    BOOKMAKER_REGIONS: str = "eu"
    MARKETS: List[str] = field(default_factory=lambda: [
        "h2h", "totals", "spreads",
    ])


# ═══════════════════════════════════════════════════════════
#  SINGLETONS
# ═══════════════════════════════════════════════════════════
api_config = APIConfig()
tg_config = TelegramConfig()
db_config = DatabaseConfig()
model_config = ModelConfig()
betting_config = BettingConfig()
