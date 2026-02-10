"""
=============================================================================
 V2.1 — CATBOOST TRAINING PIPELINE + ISOTONIC CALIBRATION

 Что внутри:
   1. Feature engineering (48 фичей на матч)
   2. CatBoost training с temporal validation
   3. Isotonic calibration (калибровка > accuracy по ROI)
   4. Бэктестинг с P&L расчётом

 Источники:
   - CatBoost + pi-ratings = лучший результат 2017 Soccer Challenge
     (0.1925 RPS, 55.82% accuracy)
   - Calibration > accuracy: ROI +34.69% vs -35.17% (arxiv:2303.06021)
   - soccerdata + penaltyblog для данных
=============================================================================
"""
import logging
import os
import pickle
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import poisson

logger = logging.getLogger(__name__)

# ─── Lazy imports (не падаем если не установлены) ───
try:
    from catboost import CatBoostClassifier, Pool
    HAS_CATBOOST = True
except ImportError:
    HAS_CATBOOST = False

try:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ═══════════════════════════════════════════════════════════
#  FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════

class FeatureEngineer:
    """
    48 фичей для каждого матча, сгруппированных по категориям:
    
    A. Сила команд (12 фичей):
       - Elo рейтинг home/away
       - Dixon-Coles attack/defence home/away
       - Pi-rating (running)
       - Elo diff, Elo ratio
       
    B. Форма (12 фичей):
       - Последние 5 матчей: wins/draws/losses, goals for/against
       - xG/xGA за последние 5 матчей (если есть)
       - Форма дома vs форма на выезде
       
    C. Head-to-Head (6 фичей):
       - H2H wins/draws/losses за 5 лет
       - H2H avg goals
       
    D. Контекст (10 фичей):
       - Дней отдыха home/away
       - Домашнее преимущество (историческое)
       - Месяц (сезонность)
       - Турнирная ситуация (разница мест)
       
    E. Рыночные (8 фичей):
       - Implied probs от Pinnacle
       - Overround (маржа)
       - Разница кф: best vs avg
       - Кол-во БК
    """

    def __init__(self):
        self._team_stats_cache: Dict[str, dict] = {}

    def compute_features(self, match: dict, history: pd.DataFrame,
                         elo_ratings: dict = None,
                         dc_params: dict = None,
                         odds_data: dict = None) -> dict:
        """
        Рассчитать все фичи для одного матча.
        
        Args:
            match: {"home", "away", "date", "league"}
            history: DataFrame с историей матчей
            elo_ratings: {"team": rating}
            dc_params: {"attack": {...}, "defence": {...}}
            odds_data: {"home": odds, "draw": odds, "away": odds}
        """
        home = match["home"]
        away = match["away"]
        date = pd.to_datetime(match["date"])
        features = {}

        # ─── A. Сила команд ───
        elo_h = (elo_ratings or {}).get(home, 1500)
        elo_a = (elo_ratings or {}).get(away, 1500)
        features["elo_home"] = elo_h
        features["elo_away"] = elo_a
        features["elo_diff"] = elo_h - elo_a
        features["elo_ratio"] = elo_h / max(elo_a, 1)

        if dc_params:
            atk = dc_params.get("attack", {})
            dfn = dc_params.get("defence", {})
            features["dc_attack_home"] = atk.get(home, 1.0)
            features["dc_attack_away"] = atk.get(away, 1.0)
            features["dc_defence_home"] = dfn.get(home, 1.0)
            features["dc_defence_away"] = dfn.get(away, 1.0)
            features["dc_expected_home_goals"] = (
                atk.get(home, 1.0) * dfn.get(away, 1.0) *
                dc_params.get("gamma", 1.25)
            )
            features["dc_expected_away_goals"] = (
                atk.get(away, 1.0) * dfn.get(home, 1.0)
            )
        else:
            for k in ["dc_attack_home", "dc_attack_away",
                       "dc_defence_home", "dc_defence_away",
                       "dc_expected_home_goals", "dc_expected_away_goals"]:
                features[k] = 0.0

        # Суммарная разница сил
        features["strength_diff"] = (
            features["dc_attack_home"] - features["dc_attack_away"] +
            features["dc_defence_away"] - features["dc_defence_home"]
        )
        features["attack_ratio"] = (
            features["dc_attack_home"] /
            max(features["dc_attack_away"], 0.01)
        )

        # ─── B. Форма (последние N матчей) ───
        for team, prefix in [(home, "home"), (away, "away")]:
            form = self._get_form(history, team, date, n=5)
            features[f"{prefix}_form_wins"] = form["wins"]
            features[f"{prefix}_form_draws"] = form["draws"]
            features[f"{prefix}_form_losses"] = form["losses"]
            features[f"{prefix}_form_gf"] = form["goals_for"]
            features[f"{prefix}_form_ga"] = form["goals_against"]
            features[f"{prefix}_form_xg"] = form.get("xg", 0)
            features[f"{prefix}_form_xga"] = form.get("xga", 0)
            features[f"{prefix}_form_points"] = (
                form["wins"] * 3 + form["draws"]
            )

        features["form_diff"] = (
            features["home_form_points"] - features["away_form_points"]
        )
        features["xg_diff"] = (
            features["home_form_xg"] - features["away_form_xg"]
        )

        # ─── C. Head-to-Head ───
        h2h = self._get_h2h(history, home, away, date, years=5)
        features["h2h_home_wins"] = h2h["home_wins"]
        features["h2h_draws"] = h2h["draws"]
        features["h2h_away_wins"] = h2h["away_wins"]
        features["h2h_total_matches"] = h2h["total"]
        features["h2h_avg_goals"] = h2h["avg_goals"]
        features["h2h_home_win_rate"] = (
            h2h["home_wins"] / max(h2h["total"], 1)
        )

        # ─── D. Контекст ───
        features["days_rest_home"] = self._days_rest(history, home, date)
        features["days_rest_away"] = self._days_rest(history, away, date)
        features["rest_advantage"] = (
            features["days_rest_home"] - features["days_rest_away"]
        )
        features["month"] = date.month
        features["is_weekend"] = int(date.weekday() >= 5)

        # Историческое домашнее преимущество
        home_hist = self._home_advantage(history, home, date)
        features["historical_home_adv"] = home_hist

        # ─── E. Рыночные фичи ───
        if odds_data:
            total_implied = sum(1.0 / v for v in odds_data.values() if v > 0)
            features["odds_home"] = odds_data.get("home", 0)
            features["odds_draw"] = odds_data.get("draw", 0)
            features["odds_away"] = odds_data.get("away", 0)
            features["implied_home"] = 1.0 / max(odds_data.get("home", 99), 0.01)
            features["implied_draw"] = 1.0 / max(odds_data.get("draw", 99), 0.01)
            features["implied_away"] = 1.0 / max(odds_data.get("away", 99), 0.01)
            features["overround"] = total_implied - 1.0
            features["num_bookmakers"] = odds_data.get("num_bk", 5)
        else:
            for k in ["odds_home", "odds_draw", "odds_away",
                       "implied_home", "implied_draw", "implied_away",
                       "overround", "num_bookmakers"]:
                features[k] = 0.0

        return features

    def _get_form(self, df: pd.DataFrame, team: str,
                  before: datetime, n: int = 5) -> dict:
        """Форма команды за последние n матчей"""
        mask = (
            ((df["home"] == team) | (df["away"] == team)) &
            (pd.to_datetime(df["date"]) < before)
        )
        recent = df[mask].sort_values("date", ascending=False).head(n)

        wins = draws = losses = gf = ga = xg = xga = 0
        for _, row in recent.iterrows():
            if row["home"] == team:
                gf += row.get("home_goals", 0)
                ga += row.get("away_goals", 0)
                xg += row.get("home_xg", 0)
                xga += row.get("away_xg", 0)
                if row.get("home_goals", 0) > row.get("away_goals", 0):
                    wins += 1
                elif row.get("home_goals", 0) == row.get("away_goals", 0):
                    draws += 1
                else:
                    losses += 1
            else:
                gf += row.get("away_goals", 0)
                ga += row.get("home_goals", 0)
                xg += row.get("away_xg", 0)
                xga += row.get("home_xg", 0)
                if row.get("away_goals", 0) > row.get("home_goals", 0):
                    wins += 1
                elif row.get("away_goals", 0) == row.get("home_goals", 0):
                    draws += 1
                else:
                    losses += 1

        return {"wins": wins, "draws": draws, "losses": losses,
                "goals_for": gf, "goals_against": ga,
                "xg": xg, "xga": xga}

    def _get_h2h(self, df, home, away, before, years=5):
        cutoff = before - timedelta(days=years * 365)
        mask = (
            (((df["home"] == home) & (df["away"] == away)) |
             ((df["home"] == away) & (df["away"] == home))) &
            (pd.to_datetime(df["date"]) < before) &
            (pd.to_datetime(df["date"]) > cutoff)
        )
        h2h = df[mask]
        hw = dw = aw = total_goals = 0
        for _, row in h2h.iterrows():
            hg = row.get("home_goals", 0)
            ag = row.get("away_goals", 0)
            total_goals += hg + ag
            real_home = row["home"]
            if hg > ag:
                if real_home == home:
                    hw += 1
                else:
                    aw += 1
            elif hg == ag:
                dw += 1
            else:
                if real_home == home:
                    aw += 1
                else:
                    hw += 1
        total = len(h2h)
        return {"home_wins": hw, "draws": dw, "away_wins": aw,
                "total": total,
                "avg_goals": total_goals / max(total, 1)}

    def _days_rest(self, df, team, before):
        mask = (
            ((df["home"] == team) | (df["away"] == team)) &
            (pd.to_datetime(df["date"]) < before)
        )
        recent = df[mask].sort_values("date", ascending=False).head(1)
        if recent.empty:
            return 7
        last_date = pd.to_datetime(recent.iloc[0]["date"])
        return (before - last_date).days

    def _home_advantage(self, df, team, before):
        mask = (df["home"] == team) & (pd.to_datetime(df["date"]) < before)
        home_games = df[mask].tail(20)
        if home_games.empty:
            return 0.5
        wins = sum(
            1 for _, r in home_games.iterrows()
            if r.get("home_goals", 0) > r.get("away_goals", 0)
        )
        return wins / len(home_games)


# ═══════════════════════════════════════════════════════════
#  CATBOOST TRAINING PIPELINE
# ═══════════════════════════════════════════════════════════

class CatBoostPipeline:
    """
    Training pipeline:
    1. Load historical data (football-data.co.uk + FBRef xG)
    2. Feature engineering (48 фичей)
    3. Temporal validation (train на прошлом, test на будущем)
    4. CatBoost training with early stopping
    5. Isotonic calibration
    6. Backtest with Kelly betting simulation
    """

    def __init__(self, model_dir: str = "models"):
        self.model_dir = model_dir
        self.feature_engineer = FeatureEngineer()
        self.model = None
        self.calibrator = None
        self._feature_names: List[str] = []

    def load_data(self, csv_path: str = None) -> pd.DataFrame:
        """
        Загрузка данных. Ожидаемый формат CSV:
        date, home, away, home_goals, away_goals, league,
        home_xg, away_xg (опционально),
        pinnacle_home, pinnacle_draw, pinnacle_away (опционально)
        
        Источники:
        - football-data.co.uk (бесплатно, CSV с кф)
        - soccerdata library: sd.FBref().read_schedule()
        """
        if csv_path and os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            logger.info(f"Loaded {len(df)} matches from {csv_path}")
            return df

        # Fallback: попробуем football-data.co.uk напрямую
        try:
            urls = [
                "https://www.football-data.co.uk/mmz4281/2324/E0.csv",
                "https://www.football-data.co.uk/mmz4281/2223/E0.csv",
                "https://www.football-data.co.uk/mmz4281/2122/E0.csv",
            ]
            dfs = []
            for url in urls:
                try:
                    temp = pd.read_csv(url)
                    temp = temp.rename(columns={
                        "HomeTeam": "home", "AwayTeam": "away",
                        "FTHG": "home_goals", "FTAG": "away_goals",
                        "Date": "date", "Div": "league",
                        "PSH": "pinnacle_home", "PSD": "pinnacle_draw",
                        "PSA": "pinnacle_away",
                    })
                    dfs.append(temp)
                except Exception:
                    pass
            if dfs:
                df = pd.concat(dfs, ignore_index=True)
                logger.info(f"Loaded {len(df)} matches from football-data.co.uk")
                return df
        except Exception as e:
            logger.warning(f"Could not load from web: {e}")

        logger.error("No data available. Provide a CSV file.")
        return pd.DataFrame()

    def build_features(self, df: pd.DataFrame,
                       elo_ratings: dict = None,
                       dc_params: dict = None) -> Tuple[pd.DataFrame, np.ndarray]:
        """Построить матрицу фичей и таргеты"""
        features_list = []
        targets = []

        for idx, row in df.iterrows():
            match = {
                "home": row["home"], "away": row["away"],
                "date": row["date"], "league": row.get("league", ""),
            }
            odds = {}
            for k in ["pinnacle_home", "pinnacle_draw", "pinnacle_away"]:
                if k in row and pd.notna(row[k]):
                    short = k.replace("pinnacle_", "")
                    odds[short] = row[k]

            feats = self.feature_engineer.compute_features(
                match, df.iloc[:idx], elo_ratings, dc_params, odds or None
            )
            features_list.append(feats)

            # Target: 0=Home, 1=Draw, 2=Away
            hg, ag = row.get("home_goals", 0), row.get("away_goals", 0)
            if hg > ag:
                targets.append(0)
            elif hg == ag:
                targets.append(1)
            else:
                targets.append(2)

        X = pd.DataFrame(features_list)
        y = np.array(targets)
        self._feature_names = list(X.columns)
        return X, y

    def train(self, X: pd.DataFrame, y: np.ndarray,
              test_size: float = 0.2) -> dict:
        """
        Обучение CatBoost с temporal split + isotonic calibration.
        
        CRITICAL: temporal validation — никакой утечки будущего!
        """
        if not HAS_CATBOOST or not HAS_SKLEARN:
            logger.error("catboost and/or sklearn not installed")
            return {"error": "Dependencies missing"}

        # Temporal split (последние 20% = тест)
        n = len(X)
        split_idx = int(n * (1 - test_size))
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Calibration split (50/50 из теста)
        cal_idx = len(X_test) // 2
        X_cal, X_eval = X_test.iloc[:cal_idx], X_test.iloc[cal_idx:]
        y_cal, y_eval = y_test[:cal_idx], y_test[cal_idx:]

        logger.info(
            f"Train: {len(X_train)}, Calibration: {len(X_cal)}, "
            f"Eval: {len(X_eval)}"
        )

        # CatBoost
        self.model = CatBoostClassifier(
            iterations=1000,
            learning_rate=0.05,
            depth=6,
            l2_leaf_reg=3,
            loss_function="MultiClass",
            classes_count=3,
            eval_metric="MultiClass",
            early_stopping_rounds=50,
            verbose=100,
            random_seed=42,
            # CatBoost's ordered target encoding prevents leakage
            boosting_type="Ordered",
        )

        train_pool = Pool(X_train, y_train)
        eval_pool = Pool(X_cal, y_cal)

        self.model.fit(
            train_pool,
            eval_set=eval_pool,
            use_best_model=True,
        )

        # Raw predictions
        probs_raw = self.model.predict_proba(X_eval)

        # ─── ISOTONIC CALIBRATION ───
        # arxiv:2303.06021: ROI +34.69% с калибровкой vs -35.17% без
        probs_cal_train = self.model.predict_proba(X_cal)
        self.calibrator = {}
        probs_calibrated = np.zeros_like(probs_raw)

        for cls in range(3):
            ir = IsotonicRegression(out_of_bounds="clip")
            ir.fit(probs_cal_train[:, cls], (y_cal == cls).astype(float))
            self.calibrator[cls] = ir
            probs_calibrated[:, cls] = ir.predict(probs_raw[:, cls])

        # Renormalize
        row_sums = probs_calibrated.sum(axis=1, keepdims=True)
        probs_calibrated = probs_calibrated / row_sums

        # ─── METRICS ───
        accuracy_raw = (probs_raw.argmax(axis=1) == y_eval).mean()
        accuracy_cal = (probs_calibrated.argmax(axis=1) == y_eval).mean()

        # Brier score (per-class, averaged)
        brier_raw = np.mean([
            brier_score_loss((y_eval == c).astype(int), probs_raw[:, c])
            for c in range(3)
        ])
        brier_cal = np.mean([
            brier_score_loss((y_eval == c).astype(int), probs_calibrated[:, c])
            for c in range(3)
        ])

        # Log loss
        ll_raw = log_loss(y_eval, probs_raw)
        ll_cal = log_loss(y_eval, probs_calibrated)

        # Feature importance
        importance = self.model.get_feature_importance()
        top_features = sorted(
            zip(self._feature_names, importance),
            key=lambda x: x[1], reverse=True,
        )[:10]

        metrics = {
            "accuracy_raw": round(accuracy_raw, 4),
            "accuracy_calibrated": round(accuracy_cal, 4),
            "brier_raw": round(brier_raw, 4),
            "brier_calibrated": round(brier_cal, 4),
            "log_loss_raw": round(ll_raw, 4),
            "log_loss_calibrated": round(ll_cal, 4),
            "train_size": len(X_train),
            "eval_size": len(X_eval),
            "top_features": top_features,
        }

        logger.info(f"Metrics: {metrics}")
        return metrics

    def predict(self, features: dict) -> dict:
        """Предсказание с калибровкой"""
        if not self.model:
            return {}
        X = pd.DataFrame([features])
        probs = self.model.predict_proba(X)[0]

        if self.calibrator:
            cal_probs = np.array([
                self.calibrator[c].predict([probs[c]])[0]
                for c in range(3)
            ])
            total = cal_probs.sum()
            if total > 0:
                cal_probs /= total
            probs = cal_probs

        return {
            "home": round(float(probs[0]), 4),
            "draw": round(float(probs[1]), 4),
            "away": round(float(probs[2]), 4),
        }

    def save(self, path: str = None):
        path = path or os.path.join(self.model_dir, "catboost_v2.cbm")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if self.model:
            self.model.save_model(path)
        cal_path = path.replace(".cbm", "_calibrator.pkl")
        if self.calibrator:
            with open(cal_path, "wb") as f:
                pickle.dump(self.calibrator, f)
        logger.info(f"Model saved to {path}")

    def load(self, path: str = None):
        if not HAS_CATBOOST:
            return
        path = path or os.path.join(self.model_dir, "catboost_v2.cbm")
        if os.path.exists(path):
            self.model = CatBoostClassifier()
            self.model.load_model(path)
            cal_path = path.replace(".cbm", "_calibrator.pkl")
            if os.path.exists(cal_path):
                with open(cal_path, "rb") as f:
                    self.calibrator = pickle.load(f)
            logger.info(f"Model loaded from {path}")


# ═══════════════════════════════════════════════════════════
#  BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════

class BacktestEngine:
    """
    Бэктестинг стратегии на исторических данных с кф.
    
    Метрики:
    - P&L (profit/loss)
    - ROI (return on investment)
    - Yield (profit per bet)
    - Brier Score / RPS
    - Calibration curve
    """

    def __init__(self, initial_bankroll: float = 1000.0,
                 kelly_fraction: float = 0.20,
                 min_edge: float = 0.03):
        self.bankroll = initial_bankroll
        self.initial = initial_bankroll
        self.kelly_fraction = kelly_fraction
        self.min_edge = min_edge
        self.bets: List[dict] = []

    def run(self, predictions: List[dict], actuals: List[int],
            odds: List[dict]) -> dict:
        """
        Args:
            predictions: [{"home": 0.5, "draw": 0.3, "away": 0.2}, ...]
            actuals: [0, 1, 2, ...]  (0=home, 1=draw, 2=away)
            odds: [{"home": 2.1, "draw": 3.4, "away": 3.6}, ...]
        """
        outcomes_map = {0: "home", 1: "draw", 2: "away"}

        for pred, actual, match_odds in zip(predictions, actuals, odds):
            if not match_odds:
                continue

            # Находим best value bet
            best_outcome = None
            best_edge = 0
            for outcome_name in ["home", "draw", "away"]:
                if outcome_name not in pred or outcome_name not in match_odds:
                    continue
                prob = pred[outcome_name]
                o = match_odds[outcome_name]
                if o <= 1:
                    continue
                edge = prob * o - 1.0
                if edge > best_edge and edge >= self.min_edge:
                    best_edge = edge
                    best_outcome = outcome_name

            if best_outcome is None:
                continue

            # Kelly stake
            prob = pred[best_outcome]
            o = match_odds[best_outcome]
            b = o - 1.0
            f_star = (b * prob - (1 - prob)) / b
            if f_star <= 0:
                continue
            stake = min(self.bankroll * f_star * self.kelly_fraction,
                        self.bankroll * 0.04)
            if stake < 1:
                continue

            # Result
            actual_name = outcomes_map[actual]
            won = (actual_name == best_outcome)
            profit = (stake * o - stake) if won else -stake
            self.bankroll += profit

            self.bets.append({
                "outcome": best_outcome,
                "prob": prob,
                "odds": o,
                "edge": best_edge,
                "stake": stake,
                "won": won,
                "profit": profit,
                "bankroll": self.bankroll,
            })

        # Summary
        if not self.bets:
            return {"error": "No bets placed"}

        total_staked = sum(b["stake"] for b in self.bets)
        total_profit = sum(b["profit"] for b in self.bets)
        wins = sum(1 for b in self.bets if b["won"])

        return {
            "total_bets": len(self.bets),
            "wins": wins,
            "win_rate": round(wins / len(self.bets), 3),
            "total_staked": round(total_staked, 2),
            "total_profit": round(total_profit, 2),
            "roi": round(total_profit / total_staked, 4) if total_staked else 0,
            "final_bankroll": round(self.bankroll, 2),
            "growth": round(
                (self.bankroll - self.initial) / self.initial, 4
            ),
            "avg_edge": round(
                np.mean([b["edge"] for b in self.bets]), 4
            ),
            "avg_odds": round(
                np.mean([b["odds"] for b in self.bets]), 2
            ),
            "max_drawdown": round(self._max_drawdown(), 3),
        }

    def _max_drawdown(self) -> float:
        if not self.bets:
            return 0
        peak = self.initial
        max_dd = 0
        for b in self.bets:
            if b["bankroll"] > peak:
                peak = b["bankroll"]
            dd = (peak - b["bankroll"]) / peak
            if dd > max_dd:
                max_dd = dd
        return max_dd
