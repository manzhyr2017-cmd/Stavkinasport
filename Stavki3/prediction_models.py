"""
=============================================================================
 BETTING ASSISTANT V2 — DIXON-COLES POISSON MODEL
 
 Базовая модель: Dixon & Coles (1997) "Modelling Association Football 
 Scores and Inefficiencies in the Football Betting Market"
 
 Улучшения:
   1. Rho-коррекция для низких счетов (0-0, 1-0, 0-1, 1-1)
   2. Экспоненциальный time-decay (свежие матчи весят больше)
   3. Параметры attack/defence для каждой команды
   4. Home advantage как глобальный параметр
   
 Формула:
   P(x, y) = tau(x, y, lambda, mu, rho) * 
             (e^{-lambda} * lambda^x / x!) * 
             (e^{-mu} * mu^y / y!)
   
   lambda = alpha_i * beta_j * gamma  (expected goals Home)
   mu = alpha_j * beta_i              (expected goals Away)
   
   alpha = attack strength, beta = defence weakness, gamma = home advantage
=============================================================================
"""
import math
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

from config.settings import model_config

logger = logging.getLogger(__name__)


class DixonColesModel:
    """
    Модель Dixon-Coles для предсказания результатов футбольных матчей.
    
    Основана на Poisson-распределении с коррекцией для 
    зависимости между голами команд при низких счетах.
    """

    def __init__(self):
        self.params: Optional[dict] = None
        self.teams: List[str] = []
        self._fitted = False
        self.rho = 0.0
        self.gamma = 1.0  # home advantage

    # ═══════════════════════════════════════════════════════
    #  CORE: Tau (коррекция Dixon-Coles)
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
        """
        Tau-функция коррекции для низких счетов.
        
        Dixon & Coles (1997) показали, что базовый Poisson 
        недооценивает ничьи и занижает 0-0, 1-0, 0-1.
        
        Args:
            x: голы Home, y: голы Away
            lam: expected goals Home, mu: expected goals Away
            rho: параметр корреляции
        """
        if x == 0 and y == 0:
            return 1.0 - lam * mu * rho
        elif x == 0 and y == 1:
            return 1.0 + lam * rho
        elif x == 1 and y == 0:
            return 1.0 + mu * rho
        elif x == 1 and y == 1:
            return 1.0 - rho
        else:
            return 1.0

    # ═══════════════════════════════════════════════════════
    #  TIME DECAY (экспоненциальное затухание)
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def time_weight(match_date: datetime, current_date: datetime,
                    xi: float = None) -> float:
        """
        Вес матча = exp(-xi * days_ago).
        
        Свежие матчи влияют сильнее. xi контролирует скорость затухания.
        xi = 0.0019 → полупериод ~365 дней (стандартное значение)
        xi = 0.005  → полупериод ~139 дней (агрессивный decay)
        """
        xi = xi or model_config.DIXON_COLES_TIME_DECAY
        days_ago = (current_date - match_date).days
        return math.exp(-xi * days_ago)

    # ═══════════════════════════════════════════════════════
    #  LOG-LIKELIHOOD (для оптимизации)
    # ═══════════════════════════════════════════════════════

    def _neg_log_likelihood(self, params_flat: np.ndarray,
                            matches: List[dict],
                            team_index: Dict[str, int]) -> float:
        """
        Негативный log-likelihood для оптимизации.
        
        Параметры (порядок в params_flat):
          - attack[0..n-1]: атака каждой команды
          - defence[0..n-1]: защита каждой команды  
          - gamma: home advantage
          - rho: корреляция Dixon-Coles
        """
        n = len(team_index)
        attack = params_flat[:n]
        defence = params_flat[n:2 * n]
        gamma = params_flat[2 * n]
        rho = params_flat[2 * n + 1]

        log_lik = 0.0
        now = datetime.utcnow()

        for match in matches:
            i = team_index[match["home"]]
            j = team_index[match["away"]]
            x = match["home_goals"]
            y = match["away_goals"]
            match_date = match.get("date", now)

            # Expected goals
            lam = max(attack[i] * defence[j] * gamma, 0.001)
            mu = max(attack[j] * defence[i], 0.001)

            # Time weight
            w = self.time_weight(match_date, now)

            # Poisson + tau correction
            tau_val = self.tau(x, y, lam, mu, rho)
            if tau_val <= 0:
                tau_val = 0.0001

            p_x = poisson.pmf(x, lam)
            p_y = poisson.pmf(y, mu)

            p = tau_val * p_x * p_y
            if p <= 0:
                p = 1e-10

            log_lik += w * math.log(p)

        return -log_lik  # минимизируем негативный

    # ═══════════════════════════════════════════════════════
    #  FIT (обучение)
    # ═══════════════════════════════════════════════════════

    def fit(self, matches: List[dict]) -> "DixonColesModel":
        """
        Обучить модель на исторических данных.
        
        Args:
            matches: список dict с ключами:
                "home", "away", "home_goals", "away_goals", "date"
        
        Returns:
            self (для chaining)
        """
        logger.info(f"Fitting Dixon-Coles on {len(matches)} matches...")

        # Собираем уникальные команды
        teams = sorted(set(
            [m["home"] for m in matches] + [m["away"] for m in matches]
        ))
        self.teams = teams
        n = len(teams)
        team_index = {t: i for i, t in enumerate(teams)}

        # Начальные параметры: attack=1, defence=1, gamma=1.25, rho=-0.03
        x0 = np.concatenate([
            np.ones(n),       # attack
            np.ones(n),       # defence
            [1.25],           # gamma (home advantage)
            [-0.03],          # rho
        ])

        # Constraint: sum(attack) = n (нормализация)
        def constraint_attack(params):
            return np.sum(params[:n]) - n

        constraints = [{"type": "eq", "fun": constraint_attack}]

        # Bounds
        bounds = (
            [(0.01, 5.0)] * n +   # attack > 0
            [(0.01, 5.0)] * n +   # defence > 0
            [(0.5, 2.5)] +        # gamma
            [model_config.DIXON_COLES_RHO_BOUNDS]  # rho
        )

        result = minimize(
            self._neg_log_likelihood,
            x0,
            args=(matches, team_index),
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 500, "ftol": 1e-8},
        )

        if result.success:
            self.params = {
                "attack": {t: result.x[i] for i, t in enumerate(teams)},
                "defence": {t: result.x[n + i] for i, t in enumerate(teams)},
                "gamma": result.x[2 * n],
                "rho": result.x[2 * n + 1],
            }
            self.rho = result.x[2 * n + 1]
            self.gamma = result.x[2 * n]
            self._fitted = True
            logger.info(
                f"Dixon-Coles fitted: gamma={self.gamma:.3f}, "
                f"rho={self.rho:.4f}, teams={n}"
            )
        else:
            logger.warning(f"Dixon-Coles optimization failed: {result.message}")
            self._fitted = False

        return self

    # ═══════════════════════════════════════════════════════
    #  PREDICT (предсказание матча)
    # ═══════════════════════════════════════════════════════

    def predict_match(self, home: str, away: str) -> Optional[dict]:
        """
        Предсказать матрицу счетов и вероятности исходов.
        
        Returns:
            {
                "score_matrix": np.array (max_goals x max_goals),
                "home_win": float,
                "draw": float, 
                "away_win": float,
                "expected_home_goals": float,
                "expected_away_goals": float,
                "over_2_5": float,
                "under_2_5": float,
            }
        """
        if not self._fitted or not self.params:
            return None

        attack = self.params["attack"]
        defence = self.params["defence"]

        if home not in attack or away not in attack:
            return None

        lam = attack[home] * defence[away] * self.gamma  # Expected Home goals
        mu = attack[away] * defence[home]                 # Expected Away goals

        max_goals = model_config.DIXON_COLES_MAX_GOALS
        matrix = np.zeros((max_goals + 1, max_goals + 1))

        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                tau_val = self.tau(x, y, lam, mu, self.rho)
                p = tau_val * poisson.pmf(x, lam) * poisson.pmf(y, mu)
                matrix[x, y] = max(p, 0)

        # Нормализуем (может быть чуть != 1 из-за усечения)
        total = matrix.sum()
        if total > 0:
            matrix /= total

        # Вероятности исходов
        home_win = np.sum(np.tril(matrix, -1))  # x > y
        draw = np.trace(matrix)
        away_win = np.sum(np.triu(matrix, 1))   # x < y

        # Тоталы
        over_2_5 = 0.0
        under_2_5 = 0.0
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                if x + y > 2:
                    over_2_5 += matrix[x, y]
                else:
                    under_2_5 += matrix[x, y]

        return {
            "score_matrix": matrix,
            "home_win": round(home_win, 4),
            "draw": round(draw, 4),
            "away_win": round(away_win, 4),
            "expected_home_goals": round(lam, 2),
            "expected_away_goals": round(mu, 2),
            "over_2_5": round(over_2_5, 4),
            "under_2_5": round(under_2_5, 4),
            "btts_yes": round(self._btts_prob(matrix), 4),
            "btts_no": round(1 - self._btts_prob(matrix), 4),
        }

    @staticmethod
    def _btts_prob(matrix: np.ndarray) -> float:
        """Both Teams To Score probability"""
        prob = 0.0
        for x in range(1, matrix.shape[0]):
            for y in range(1, matrix.shape[1]):
                prob += matrix[x, y]
        return prob

    def predict_probabilities(self, home: str, away: str) -> dict:
        """
        Возвращает вероятности для Value Engine.
        
        Returns:
            {"home": 0.45, "draw": 0.28, "away": 0.27, 
             "over": 0.55, "under": 0.45}
        """
        pred = self.predict_match(home, away)
        if not pred:
            return {}
        return {
            "home": pred["home_win"],
            "draw": pred["draw"],
            "away": pred["away_win"],
            "over": pred["over_2_5"],
            "under": pred["under_2_5"],
        }

    def most_likely_scores(self, home: str, away: str,
                           top_n: int = 5) -> List[Tuple[int, int, float]]:
        """Top-N наиболее вероятных счетов"""
        pred = self.predict_match(home, away)
        if not pred:
            return []
        matrix = pred["score_matrix"]
        scores = []
        for x in range(matrix.shape[0]):
            for y in range(matrix.shape[1]):
                scores.append((x, y, matrix[x, y]))
        scores.sort(key=lambda s: s[2], reverse=True)
        return scores[:top_n]


# ═══════════════════════════════════════════════════════════
#  ELO RATING SYSTEM
# ═══════════════════════════════════════════════════════════

class EloRatingSystem:
    """
    Elo-рейтинг команд с учётом:
    - Home advantage (+65 Elo по умолчанию)
    - Margin of victory (множитель за разницу голов)
    - K-factor decay (снижение K для устоявшихся команд)
    
    Источник: FiveThirtyEight Soccer Power Index methodology
    """

    def __init__(self):
        self.ratings: Dict[str, float] = {}
        self.games_played: Dict[str, int] = {}
        self.initial = model_config.ELO_INITIAL
        self.k = model_config.ELO_K_FACTOR
        self.home_adv = model_config.ELO_HOME_ADVANTAGE

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial)

    def expected_score(self, home: str, away: str) -> Dict[str, float]:
        """
        Ожидаемый результат на основе Elo-рейтингов.
        
        P(Home) = 1 / (1 + 10^((R_away - R_home - HOME_ADV) / 400))
        
        Returns:
            {"home": P, "draw": ~0, "away": 1-P}
        """
        r_home = self.get_rating(home) + self.home_adv
        r_away = self.get_rating(away)
        
        e_home = 1.0 / (1.0 + 10 ** ((r_away - r_home) / 400.0))
        e_away = 1.0 - e_home

        # Elo не умеет предсказывать ничью напрямую,
        # аппроксимируем: ничья вероятнее, когда команды близки по силе
        diff = abs(r_home - r_away)
        draw_factor = max(0.05, 0.28 * math.exp(-diff / 300.0))

        p_home = e_home * (1 - draw_factor)
        p_away = e_away * (1 - draw_factor)
        p_draw = draw_factor

        return {
            "home": round(p_home, 4),
            "draw": round(p_draw, 4),
            "away": round(p_away, 4),
        }

    def update(self, home: str, away: str,
               home_goals: int, away_goals: int):
        """Обновить рейтинги после матча"""
        if home not in self.ratings:
            self.ratings[home] = self.initial
            self.games_played[home] = 0
        if away not in self.ratings:
            self.ratings[away] = self.initial
            self.games_played[away] = 0

        # Actual score (1 = win, 0.5 = draw, 0 = loss)
        if home_goals > away_goals:
            s_home, s_away = 1.0, 0.0
        elif home_goals == away_goals:
            s_home, s_away = 0.5, 0.5
        else:
            s_home, s_away = 0.0, 1.0

        # Expected
        exp = self.expected_score(home, away)
        e_home = exp["home"] + exp["draw"] * 0.5
        e_away = exp["away"] + exp["draw"] * 0.5

        # Margin of Victory multiplier (FiveThirtyEight style)
        goal_diff = abs(home_goals - away_goals)
        if model_config.ELO_MARGIN_MULTIPLIER:
            mov = math.log(goal_diff + 1) * (2.2 / (
                (self.get_rating(home if s_home > s_away else away)
                 - self.get_rating(away if s_home > s_away else home)) * 0.001 + 2.2
            ))
        else:
            mov = 1.0

        # K-factor (lower for well-established teams)
        k_home = self.k / (1 + self.games_played[home] / 100)
        k_away = self.k / (1 + self.games_played[away] / 100)

        # Update
        self.ratings[home] += k_home * mov * (s_home - e_home)
        self.ratings[away] += k_away * mov * (s_away - e_away)
        self.games_played[home] += 1
        self.games_played[away] += 1

    def fit(self, matches: List[dict]) -> "EloRatingSystem":
        """Прогнать все исторические матчи"""
        for m in sorted(matches, key=lambda x: x.get("date", datetime.min)):
            self.update(m["home"], m["away"], m["home_goals"], m["away_goals"])
        logger.info(f"Elo fitted: {len(self.ratings)} teams")
        return self


# ═══════════════════════════════════════════════════════════
#  ENSEMBLE PREDICTOR (объединение моделей)
# ═══════════════════════════════════════════════════════════

class EnsemblePredictor:
    """
    Объединяет предсказания нескольких моделей:
    1. Dixon-Coles (Poisson + коррекция)
    2. Elo Rating System
    3. Market Consensus (implied probabilities от 10+ БК)
    4. CatBoost (если обучена)
    
    Источник: arxiv.org/pdf/2303.06021 показал, что калибровка 
    важнее accuracy (ROI +34.69% vs -35.17%).
    """

    def __init__(
        self,
        dixon_coles: Optional[DixonColesModel] = None,
        elo: Optional[EloRatingSystem] = None,
    ):
        self.dc = dixon_coles
        self.elo = elo
        self.weights = model_config.ENSEMBLE_WEIGHTS

    def predict(self, home: str, away: str,
                market_probs: Optional[dict] = None) -> dict:
        """
        Взвешенное предсказание ансамбля.
        
        Args:
            home, away: названия команд
            market_probs: {"home": P, "draw": P, "away": P} от БК
        
        Returns:
            {"home": P, "draw": P, "away": P, "sources": {...}}
        """
        predictions = {}
        total_weight = 0.0

        # 1. Dixon-Coles
        if self.dc and self.dc._fitted:
            dc_pred = self.dc.predict_probabilities(home, away)
            if dc_pred:
                predictions["dixon_coles"] = dc_pred
                total_weight += self.weights.get("dixon_coles", 0)

        # 2. Elo
        if self.elo:
            elo_pred = self.elo.expected_score(home, away)
            predictions["elo"] = elo_pred
            total_weight += self.weights.get("elo", 0)

        # 3. Market Consensus
        if market_probs:
            predictions["market_consensus"] = market_probs
            total_weight += self.weights.get("market_consensus", 0)

        if not predictions or total_weight == 0:
            return {}

        # Weighted average
        ensemble = {"home": 0.0, "draw": 0.0, "away": 0.0}
        for source, pred in predictions.items():
            w = self.weights.get(source, 0) / total_weight
            for outcome in ["home", "draw", "away"]:
                if outcome in pred:
                    ensemble[outcome] += pred[outcome] * w

        # Нормализация до 1.0
        total = sum(ensemble.values())
        if total > 0:
            ensemble = {k: round(v / total, 4) for k, v in ensemble.items()}

        ensemble["sources"] = predictions
        ensemble["model_count"] = len(predictions)

        return ensemble
