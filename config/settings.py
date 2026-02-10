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
    ODDS_API_HOST: str = "https://api.the-odds-api.com"
    ODDS_API_BASE: str = "https://api.the-odds-api.com/v4"
    RU_MODE: bool = True  # Включить режим российских БК (Фонбет)
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
    DIXON_COLES_SEASONS: int = 4                     # Сколько сезонов для обучения (Увеличено)
    USE_XG_FOR_TRAINING: bool = True                # Использовать xG вместо голов (стабильнее)

    # --- Elo Rating ---
    ELO_INITIAL: float = 1500.0
    ELO_K_FACTOR: float = 32.0       # Скорость обновления рейтинга
    ELO_HOME_ADVANTAGE: float = 65.0  # Бонус домашней команде (в Elo-пунктах)
    ELO_MARGIN_MULTIPLIER: bool = True  # Учитывать разницу голов

    # --- CatBoost (если есть обученная модель) ---
    CATBOOST_MODEL_PATH: str = "models/catboost_value.cbm"
    USE_CATBOOST: bool = True  # Enabled for Phase 13

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
    MIN_VALUE_EDGE: float = 0.02         # 2% edge — фильтруем слабые сигналы
    MIN_CONFIRMED_EDGE: float = 0.05     # 5% edge — подтверждённый (2+ модели)
    MAX_VALUE_EDGE: float = 0.60         # >60% — подозрительно
    MIN_ODDS: float = 1.05               # Любой кф
    MAX_ODDS: float = 20.00

    # --- Авто-ставки ---
    AUTO_BET: bool = True                # Автоматическое размещение ставок
    AUTO_BET_MIN_EDGE: float = 0.05      # Мин. edge для авто-ставок (5%)
    AUTO_BET_MIN_CONFIDENCE: str = "High" # Мин. уверенность (только High/Medium)
    AUTO_BET_MAX_PER_SCAN: int = 5       # Макс. кол-во авто-ставок за один скан

    # --- Фильтры качества ---
    MIN_BOOKMAKERS: int = 1       # Работаем только с Fonbet
    MIN_MODEL_AGREEMENT: int = 1  # Достаточно одной модели
    SHARP_BOOKMAKER: str = ""     # Pinnacle недоступен

    # --- Экспресс (аккумулятор) ---
    EXPRESS_MIN_EVENTS: int = 2
    EXPRESS_MAX_EVENTS: int = 5    # Оптимально 2-5 событий
    EXPRESS_MIN_LEG_PROB: float = 0.05   # Снижено для глубоких underdogs
    EXPRESS_MAX_LEG_ODDS: float = 25.00  # Разрешаем очень высокие кф
    EXPRESS_MIN_LEG_EDGE: float = -0.05  # Edge >= -5% (допускаем съедание маржи)
    EXPRESS_MAX_TOTAL_ODDS: float = 500.0 # Разрешаем "паровозы" с огромным кф 

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

    # --- Bankroll Management (RUB) ---
    INITIAL_BANKROLL: float = float(os.getenv("INITIAL_BANKROLL", "100000"))
    KELLY_FRACTION: float = 0.15     # Консервативный Келли
    MAX_BET_PERCENT: float = 0.05    # Макс. 5% банка на ставку
    MAX_EXPRESS_BET_PERCENT: float = 0.03 # 3% на экспресс
    MAX_SYSTEM_BET_PERCENT: float = 0.03
    MIN_BET_AMOUNT: float = 100.0    # Минимальная ставка 100₽

    # --- Stop-Loss ---
    MAX_DAILY_LOSS_PERCENT: float = 0.08     # 8% банка
    MAX_WEEKLY_LOSS_PERCENT: float = 0.15    # 15% банка
    MAX_LOSING_STREAK: int = 7               # 7 проигрышей подряд = пауза
    BANKRUPTCY_THRESHOLD: float = 0.15       # <15% начального = стоп

    # --- Мониторинг ---
    ODDS_POLL_INTERVAL: int = 900       # 15 мин. (оптимизация лимитов)
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
        "soccer_portugal_primeira_liga",
        "soccer_turkey_super_league",
        "soccer_brazil_campeonato",
        "soccer_netherlands_eredivisie",
        "soccer_efl_champ",
        "soccer_russia_premier_league",
    ])
    
    # Приоритетные букмекеры для РФ рынка
    PRIORITY_BOOKMAKERS: List[str] = field(default_factory=lambda: [
        "Winline", "Fonbet", "BetBoom", "Pari", "Liga Stavok",
        "1xBet", "Marathon Bet", "Betcity", "Leon", "Olimpbet",
        "Melbet", "Tennisi", "Zenit", "Baltbet", "Sportbet",
        "Pinnacle"
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
