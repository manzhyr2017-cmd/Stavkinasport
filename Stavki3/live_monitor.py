"""
=============================================================================
 V2.3 — LSTM LINE PREDICTION + LIVE WEBSOCKET MONITOR

 Модули:
   1. OddsTimeSeriesCollector — собирает таймсерии движения линий
   2. LSTMLinePredictor — LSTM для предсказания движения кф
   3. LiveOddsMonitor — WebSocket/polling мониторинг в реальном времени
   4. SharpMoneyDetector — детектирование "умных денег"

 Идея:
   - Кф меняются перед матчем (steam moves, sharp money)
   - LSTM учится на паттернах движений → предсказывает будущие drops
   - Если LSTM предсказывает drop → ставим ДО того, как линия упадёт
=============================================================================
"""
import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


# ═══════════════════════════════════════════════════════════
#  ODDS TIME SERIES COLLECTOR
# ═══════════════════════════════════════════════════════════

class OddsTimeSeriesCollector:
    """
    Собирает историю изменений коэффициентов для каждого матча.
    
    Формат: {match_id: [(timestamp, home, draw, away), ...]}
    
    Используется для:
    1. Обучения LSTM
    2. Детектирования sharp moves
    3. Steam move alerts
    """

    def __init__(self, max_history: int = 500):
        self.series: Dict[str, List[Tuple[float, dict]]] = {}
        self.max_history = max_history

    def record(self, match_id: str, odds: dict):
        """Записать текущие кф"""
        ts = time.time()
        if match_id not in self.series:
            self.series[match_id] = []
        self.series[match_id].append((ts, odds.copy()))

        # Trim old entries
        if len(self.series[match_id]) > self.max_history:
            self.series[match_id] = self.series[match_id][-self.max_history:]

    def get_series(self, match_id: str) -> List[Tuple[float, dict]]:
        return self.series.get(match_id, [])

    def to_numpy(self, match_id: str, outcome: str = "home",
                 seq_len: int = 20) -> Optional[np.ndarray]:
        """
        Конвертация в numpy массив для LSTM.
        
        Returns:
            shape (seq_len, 4): [timestamp_delta, home, draw, away]
        """
        series = self.get_series(match_id)
        if len(series) < seq_len:
            return None

        recent = series[-seq_len:]
        arr = np.zeros((seq_len, 4))
        t0 = recent[0][0]

        for i, (ts, odds) in enumerate(recent):
            arr[i, 0] = (ts - t0) / 3600.0  # Часы от начала
            arr[i, 1] = odds.get("home", 0)
            arr[i, 2] = odds.get("draw", 0)
            arr[i, 3] = odds.get("away", 0)

        return arr

    def get_movement_features(self, match_id: str) -> dict:
        """
        Фичи движения линии для ML:
        - velocity (скорость изменения)
        - acceleration
        - volatility (std dev)
        - trend direction
        """
        series = self.get_series(match_id)
        if len(series) < 3:
            return {}

        home_odds = [s[1].get("home", 0) for s in series]
        timestamps = [s[0] for s in series]

        if len(home_odds) < 3:
            return {}

        # Velocity (change per hour)
        dt = (timestamps[-1] - timestamps[0]) / 3600 or 1
        velocity = (home_odds[-1] - home_odds[0]) / dt

        # Acceleration
        mid = len(home_odds) // 2
        v1 = (home_odds[mid] - home_odds[0]) / max(mid, 1)
        v2 = (home_odds[-1] - home_odds[mid]) / max(len(home_odds) - mid, 1)
        acceleration = v2 - v1

        # Volatility
        volatility = float(np.std(home_odds)) if len(home_odds) > 1 else 0

        # Direction: negative = dropping (sharp money on home)
        direction = "dropping" if velocity < -0.01 else (
            "rising" if velocity > 0.01 else "stable"
        )

        return {
            "velocity": round(velocity, 4),
            "acceleration": round(acceleration, 4),
            "volatility": round(volatility, 4),
            "direction": direction,
            "num_snapshots": len(series),
            "total_change_pct": round(
                (home_odds[-1] / home_odds[0] - 1) * 100
                if home_odds[0] > 0 else 0, 2
            ),
        }


# ═══════════════════════════════════════════════════════════
#  LSTM LINE PREDICTOR
# ═══════════════════════════════════════════════════════════

if HAS_TORCH:
    class OddsLSTM(nn.Module):
        """
        LSTM модель для предсказания движения коэффициентов.
        
        Input: (batch, seq_len, 4)  — [time_delta, home, draw, away]
        Output: (batch, 3) — предсказанные [home, draw, away] через 1 час
        """

        def __init__(self, input_size=4, hidden_size=64,
                     num_layers=2, output_size=3, dropout=0.2):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size, hidden_size, num_layers,
                batch_first=True, dropout=dropout if num_layers > 1 else 0,
            )
            self.fc = nn.Sequential(
                nn.Linear(hidden_size, 32),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(32, output_size),
            )

        def forward(self, x):
            lstm_out, _ = self.lstm(x)
            last = lstm_out[:, -1, :]
            return self.fc(last)


class LSTMLinePredictor:
    """
    Обёртка над OddsLSTM для предсказания и обучения.
    
    Цель: предсказать кф через 1 час.
    Если предсказанный Home кф < текущего → линия упадёт → 
    ставим сейчас, пока кф высокий.
    """

    def __init__(self, model_path: str = "models/odds_lstm.pt"):
        self.model_path = model_path
        self.model = None
        self.seq_len = 20

        if HAS_TORCH:
            self.model = OddsLSTM()
            self._load_if_exists()

    def _load_if_exists(self):
        import os
        if os.path.exists(self.model_path):
            try:
                self.model.load_state_dict(
                    torch.load(self.model_path, map_location="cpu")
                )
                self.model.eval()
                logger.info("LSTM model loaded")
            except Exception as e:
                logger.warning(f"Could not load LSTM: {e}")

    def predict_next_odds(self, series_array: np.ndarray) -> Optional[dict]:
        """
        Предсказать кф через 1 час.
        
        Args:
            series_array: shape (seq_len, 4)
        
        Returns:
            {"home": predicted, "draw": predicted, "away": predicted,
             "home_change": pct_change}
        """
        if not HAS_TORCH or self.model is None:
            return None
        if series_array is None or len(series_array) < self.seq_len:
            return None

        try:
            self.model.eval()
            x = torch.FloatTensor(series_array).unsqueeze(0)
            with torch.no_grad():
                pred = self.model(x).squeeze(0).numpy()

            current = series_array[-1]
            return {
                "home": round(float(pred[0]), 3),
                "draw": round(float(pred[1]), 3),
                "away": round(float(pred[2]), 3),
                "home_change_pct": round(
                    (pred[0] / current[1] - 1) * 100
                    if current[1] > 0 else 0, 2
                ),
                "will_drop": bool(pred[0] < current[1] * 0.97),
            }
        except Exception as e:
            logger.warning(f"LSTM prediction error: {e}")
            return None

    def train(self, data: List[np.ndarray], targets: List[np.ndarray],
              epochs: int = 50, lr: float = 0.001):
        """
        Обучение на исторических данных движения линий.
        
        data: list of shape (seq_len, 4)
        targets: list of shape (3,) — кф через 1 час
        """
        if not HAS_TORCH:
            logger.error("PyTorch not installed")
            return

        X = torch.FloatTensor(np.array(data))
        Y = torch.FloatTensor(np.array(targets))

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        loss_fn = nn.MSELoss()

        self.model.train()
        for epoch in range(epochs):
            optimizer.zero_grad()
            pred = self.model(X)
            loss = loss_fn(pred, Y)
            loss.backward()
            optimizer.step()

            if (epoch + 1) % 10 == 0:
                logger.info(f"LSTM Epoch {epoch + 1}/{epochs}, Loss: {loss.item():.6f}")

        # Save
        import os
        os.makedirs(os.path.dirname(self.model_path) or ".", exist_ok=True)
        torch.save(self.model.state_dict(), self.model_path)
        logger.info(f"LSTM saved to {self.model_path}")


# ═══════════════════════════════════════════════════════════
#  SHARP MONEY DETECTOR
# ═══════════════════════════════════════════════════════════

class SharpMoneyDetector:
    """
    Детектирование "умных денег" (sharp money) по паттернам.
    
    Признаки sharp money:
    1. Быстрое падение кф у Pinnacle (>3% за <30 мин)
    2. Движение против public money (reverse line movement)
    3. Кф падает при сохранении/росте кф у рекреационных БК
    4. Steam move: одновременное движение у нескольких шарпов
    
    Источник: Pinnacle (самый шарповый БК) задаёт тренд,
    остальные подстраиваются через 30-60 мин.
    """

    SHARP_BOOKMAKERS = ["Pinnacle", "Betfair Exchange", "Sbobet", "Matchbook"]
    SOFT_BOOKMAKERS = ["bet365", "William Hill", "Ladbrokes", "Coral",
                        "Betway", "Unibet"]

    def __init__(self, collector: OddsTimeSeriesCollector = None):
        self.collector = collector or OddsTimeSeriesCollector()

    def detect(self, match_id: str,
               current_odds_by_bk: Dict[str, dict]) -> dict:
        """
        Анализ текущих кф по БК на предмет sharp money.
        
        Args:
            current_odds_by_bk: {"Pinnacle": {"home": 2.1, ...}, ...}
        
        Returns:
            {"detected": bool, "direction": str, "confidence": float,
             "details": str}
        """
        sharp_odds = {}
        soft_odds = {}

        for bk, odds in current_odds_by_bk.items():
            if any(s.lower() in bk.lower() for s in self.SHARP_BOOKMAKERS):
                sharp_odds[bk] = odds
            elif any(s.lower() in bk.lower() for s in self.SOFT_BOOKMAKERS):
                soft_odds[bk] = odds

        if not sharp_odds or not soft_odds:
            return {"detected": False, "confidence": 0}

        # Compare sharp vs soft
        for outcome in ["home", "draw", "away"]:
            sharp_avg = np.mean([
                o.get(outcome, 0) for o in sharp_odds.values() if o.get(outcome, 0) > 0
            ])
            soft_avg = np.mean([
                o.get(outcome, 0) for o in soft_odds.values() if o.get(outcome, 0) > 0
            ])

            if sharp_avg <= 0 or soft_avg <= 0:
                continue

            # Sharp кф значительно ниже soft → sharp money на этот исход
            diff_pct = (soft_avg - sharp_avg) / soft_avg * 100
            if diff_pct > 5:
                return {
                    "detected": True,
                    "direction": outcome,
                    "confidence": min(diff_pct / 10, 1.0),
                    "sharp_avg": round(sharp_avg, 3),
                    "soft_avg": round(soft_avg, 3),
                    "diff_pct": round(diff_pct, 1),
                    "details": (
                        f"Sharp money on {outcome}: "
                        f"sharp avg {sharp_avg:.2f} vs soft avg {soft_avg:.2f} "
                        f"({diff_pct:.1f}% gap)"
                    ),
                }

        return {"detected": False, "confidence": 0}


# ═══════════════════════════════════════════════════════════
#  LIVE MONITOR (async polling loop)
# ═══════════════════════════════════════════════════════════

class LiveOddsMonitor:
    """
    Real-time мониторинг с adaptive polling:
    - Далеко от kickoff → poll каждые 5 мин
    - 2 часа до → каждые 2 мин
    - 30 мин до → каждые 30 сек
    - Sharp move → немедленный alert
    """

    def __init__(self, odds_fetcher=None, on_alert: Callable = None):
        self.fetcher = odds_fetcher
        self.collector = OddsTimeSeriesCollector()
        self.sharp_detector = SharpMoneyDetector(self.collector)
        self.lstm = LSTMLinePredictor()
        self.on_alert = on_alert
        self._running = False

    def get_poll_interval(self, kickoff: datetime) -> int:
        """Adaptive polling interval"""
        delta = (kickoff - datetime.utcnow()).total_seconds()
        if delta > 7200:    # >2h
            return 300      # 5 min
        elif delta > 1800:  # >30min
            return 120      # 2 min
        elif delta > 0:     # <30min
            return 30       # 30 sec
        else:
            return 60       # Post-kickoff

    async def monitor_match(self, match_id: str, kickoff: datetime):
        """Мониторинг одного матча"""
        self._running = True
        logger.info(f"Monitoring {match_id} until kickoff {kickoff}")

        while self._running and datetime.utcnow() < kickoff + timedelta(minutes=5):
            interval = self.get_poll_interval(kickoff)

            try:
                if self.fetcher:
                    # Fetch current odds (implementation depends on fetcher)
                    pass

                # LSTM prediction
                arr = self.collector.to_numpy(match_id)
                if arr is not None:
                    pred = self.lstm.predict_next_odds(arr)
                    if pred and pred.get("will_drop"):
                        logger.info(
                            f"⚡ LSTM predicts drop for {match_id}: "
                            f"{pred['home_change_pct']:+.1f}%"
                        )
                        if self.on_alert:
                            await self.on_alert({
                                "type": "lstm_drop",
                                "match_id": match_id,
                                "prediction": pred,
                            })

            except Exception as e:
                logger.error(f"Monitor error: {e}")

            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
