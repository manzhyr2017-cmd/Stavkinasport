# 🤖 AI Betting Assistant V2 — Production-Grade System

## Что нового в V2 (vs V1)

| Компонент | V1 | V2 |
|-----------|----|----|
| Предсказания | Только implied probability | **Dixon-Coles + Elo + Market Ensemble** |
| Снятие маржи | Basic normalization | **4 метода: Shin, Power, Additive, Multiplicative** |
| Kelly | Фиксированный fraction | **Adaptive Kelly** (снижается при losing streak/drawdown) |
| Экспрессы | Независимые ноги | **Correlation-aware** (штрафы за одну лигу/день) |
| Фильтрация | Только edge | **Multi-model confirmation + Pinnacle (sharp) check** |
| Stop-loss | Daily/weekly | **+ Losing streak (7+) + Drawdown (30%) + Bankruptcy** |
| Калибровка | Нет | **Isotonic regression / Platt scaling** |

---

## Архитектура V2

```
┌────────────────────────────────────────────────────────────┐
│                     TELEGRAM BOT                            │
│                  (aiogram 3.x + inline KB)                  │
└───────────────────────────┬────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────┐
│                  SIGNAL GENERATOR                           │
│           (orchestrator — scan → analyze → notify)          │
└────────┬──────────────────┬──────────────────┬─────────────┘
         │                  │                  │
┌────────▼────────┐ ┌──────▼───────┐ ┌────────▼──────────┐
│  VALUE ENGINE   │ │  ENSEMBLE    │ │  BANKROLL MANAGER  │
│ • 4x overround  │ │  PREDICTOR   │ │ • Adaptive Kelly   │
│ • corr. express │ │ ┌──────────┐ │ │ • Losing streak    │
│ • sharp check   │ │ │Dixon-Cole│ │ │ • Drawdown protect │
│ • line movement │ │ │Elo Rating│ │ │ • Multi stop-loss  │
│                 │ │ │Market Avg│ │ │                    │
│                 │ │ │CatBoost  │ │ │                    │
│                 │ │ └──────────┘ │ │                    │
└─────────────────┘ └──────────────┘ └────────────────────┘
         │
┌────────▼──────────────────────────────────────────────────┐
│                    DATA LAYER                              │
│  The Odds API → Redis (live cache) → PostgreSQL (history) │
└───────────────────────────────────────────────────────────┘
```

---

## Ключевые алгоритмы

### 1. Dixon-Coles Poisson Model (1997)

Золотой стандарт предсказания футбольных матчей. Модель строит матрицу вероятностей всех счетов (от 0-0 до 7-7) с учётом:

- **Attack/Defence** параметры для каждой команды
- **Home advantage** (γ ≈ 1.25)
- **Rho-коррекция** (τ) для низких счетов — Poisson занижает 0-0, 0-1, 1-0
- **Time decay** — свежие матчи весят больше (экспоненциальное затухание ξ)

### 2. Shin's Method (снятие маржи)

Лучший метод удаления overround из коэффициентов БК. Учитывает favourite-longshot bias и долю "инсайдерских" ставок (z):

```
z = (overround - 1) / (n - 1)
P_fair = (√(z² + 4(1-z)·implied²/overround) - z) / (2(1-z))
```

Для сравнения: basic normalization просто делит на overround — грубо, не учитывает bias.

### 3. Correlation-Aware Express Builder

Стандартная формула экспресса (P = ∏Pᵢ) предполагает независимость, но матчи одной лиги коррелируют через погоду, судей и турнирную ситуацию. V2 вводит дисконты:

- Каждая доп. нога: ×0.95
- Ноги из одной лиги: ×0.90 за каждую пару
- Ноги в один день: ×0.97

### 4. Adaptive Kelly Criterion

Базовый fraction (0.20) автоматически снижается:
- 3-4 проигрыша подряд: fraction ×0.75
- 5+ проигрышей: fraction ×0.50
- Drawdown >10%: fraction ×0.75
- Drawdown >15%: fraction ×0.50

Источник: arxiv.org/pdf/2107.08827 показал, что fractional Kelly с adaptive sizing превосходит фиксированный fraction.

### 5. Probability Calibration

Исследование arxiv.org/pdf/2303.06021 доказало: **калибровка важнее accuracy** (ROI +34.69% vs -35.17% при выборе модели по accuracy). Используем isotonic regression для калибровки выходов модели.

---

## Структура проекта

```
betting-v2/
├── main.py                     # Entry point (--scan-once, --bot-only)
├── config/
│   └── settings.py             # ВСЕ настройки (API, модели, стратегия, bankroll)
├── core/
│   ├── prediction_models.py    # V2.0: Dixon-Coles + Elo + Ensemble Predictor
│   ├── value_engine.py         # V2.0: 4x overround removal + corr. expresses
│   ├── bankroll.py             # V2.0: Adaptive Kelly + multi stop-loss
│   ├── signal_generator.py     # V2.0: Orchestrator
│   ├── models.py               # V2.0: Domain models
│   ├── ml_pipeline.py          # V2.1: CatBoost + Isotonic Calibration + Backtest
│   ├── nlp_xg_module.py        # V2.2: FBRef xG + NLP Injury Scanner
│   └── live_monitor.py         # V2.3: LSTM + Sharp Money + Live Monitor
├── data/
│   ├── odds_fetcher.py         # The Odds API async client + Redis
│   └── database.py             # PostgreSQL (SQLAlchemy async)
├── bot/
│   └── telegram_bot.py         # Aiogram 3.x (commands + notifications)
├── dashboard/
│   └── app.py                  # V2.4: FastAPI + React dashboard
├── models/                     # Trained ML models (.cbm, .pt)
├── .env.example
├── requirements.txt            # All phases dependencies
├── Dockerfile
└── docker-compose.yml          # bot + postgres + redis
```

---

## Быстрый старт

### 1. API ключи

| Сервис | Бесплатно | Что даёт |
|--------|-----------|----------|
| [The Odds API](https://the-odds-api.com) | 500 req/мес | Live-кф 15+ БК |
| [@BotFather](https://t.me/BotFather) | ∞ | Telegram бот |
| [football-data.org](https://football-data.org) | 10 req/мин | Исторические данные (для Dixon-Coles) |

### 2. Настройка

```bash
cp .env.example .env
# Заполните ключи в .env
```

### 3. Запуск

```bash
# Docker (рекомендуется)
docker-compose up -d

# Или вручную
pip install -r requirements.txt
python main.py --scan-once   # Тест
python main.py               # Полный режим
```

---

## Telegram команды

| Команда | Что делает |
|---------|------------|
| `/start` | Главное меню |
| `/scan` | Сканирование (админ) |
| `/bankroll` | Статистика банкролла |
| `/settings` | Текущие настройки |
| `/stop` / `/resume` | Управление мониторингом |

---

## Настраиваемые параметры

Все в `config/settings.py`:

```python
# Модель
DIXON_COLES_TIME_DECAY = 0.0019  # Скорость затухания старых матчей
ELO_K_FACTOR = 32.0              # Чувствительность Elo
ENSEMBLE_WEIGHTS = {              # Веса моделей
    "dixon_coles": 0.35,
    "elo": 0.15,
    "market_consensus": 0.40,
    "catboost": 0.10,
}

# Стратегия
MIN_VALUE_EDGE = 0.03            # 3% мин. edge
KELLY_FRACTION = 0.20            # 1/5 Kelly
EXPRESS_CORRELATION_DISCOUNT = 0.95

# Risk
MAX_DAILY_LOSS_PERCENT = 0.08    # 8% daily stop
MAX_LOSING_STREAK = 7            # Auto-pause
```

---

## Все фазы (V2.0 → V2.4)

### ✅ V2.0 — Базовая система
Dixon-Coles Poisson Model, Elo Rating, Shin's overround removal, Adaptive Kelly, Correlation-aware экспрессы, multi-model confirmation.

### ✅ V2.1 — CatBoost + Calibration (`core/ml_pipeline.py`)
- **FeatureEngineer** — 48 фичей на матч: Elo, Dixon-Coles attack/defence, форма (5 матчей), H2H (5 лет), xG/xGA, контекст (отдых, месяц), рыночные (implied, overround)
- **CatBoostPipeline** — temporal validation (никакой утечки будущего!), CatBoost с ordered boosting, early stopping
- **Isotonic Calibration** — калибровка вероятностей (ROI +34.69% vs -35.17% без калибровки, arxiv:2303.06021)
- **BacktestEngine** — симуляция Kelly-стратегии на исторических кф, P&L, ROI, max drawdown

### ✅ V2.2 — NLP + xG Data (`core/nlp_xg_module.py`)
- **XGDataFetcher** — 3 метода получения xG: soccerdata library, прямой FBRef scrape, Understat JSON API
- **InjuryScanner** — мониторинг RSS (BBC Sport), keyword extraction для ключевых игроков, impact scoring (0-1)
- **TeamContextBuilder** — объединяет xG-статистику + травмы в корректировку вероятностей

### ✅ V2.3 — Live Monitor + LSTM (`core/live_monitor.py`)
- **OddsTimeSeriesCollector** — таймсерии движения кф для каждого матча
- **LSTMLinePredictor** — LSTM (PyTorch) предсказывает кф через 1 час; если предсказан drop → ставим ДО падения
- **SharpMoneyDetector** — сравнение Pinnacle vs bet365: gap >5% = sharp money detected
- **LiveOddsMonitor** — adaptive polling (5мин → 2мин → 30сек перед kickoff)

### ✅ V2.4 — Web Dashboard (`dashboard/app.py`)
- **FastAPI backend** — REST API + WebSocket live updates
- **React frontend** — Tailwind CSS, bankroll cards, signals table, express cards
- **Endpoints**: /api/signals, /api/expresses, /api/bankroll, /api/scan, /ws/live
- Запуск: `uvicorn dashboard.app:app --port 8000`

---

## Roadmap (следующие шаги)

- [ ] **V3.0** — Browser automation (Playwright + anti-detect profiles)
- [ ] **V3.1** — Telegram inline mode + subscription model
- [ ] **V3.2** — Multi-sport expansion (NBA, Tennis)
- [ ] **V3.3** — Distributed scanning (Celery + Redis queues)
- [ ] **V3.0** — Browser automation (Playwright + anti-detect profiles)
