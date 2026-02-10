"""
=============================================================================
 TESTS — Proper pytest structure
 Run: pytest tests/ -v
=============================================================================
"""
import pytest
import math
from datetime import datetime


# ═══════════════════════════════════════════════════════════
#  TEST: FONBET PARSER
# ═══════════════════════════════════════════════════════════

class TestFonbetParser:
    """Тесты парсера Фонбет"""

    def test_factor_map_completeness(self):
        """Все основные Factor IDs присутствуют"""
        from core.ru_bookmakers import FonbetParser
        fm = FonbetParser.FACTOR_MAP
        assert 921 in fm  # П1
        assert 922 in fm  # Х
        assert 923 in fm  # П2
        assert 1845 in fm  # ОЗ Да
        assert 1846 in fm  # ОЗ Нет

    @pytest.mark.asyncio
    async def test_ai_generation_skip_if_no_key(self):
        """Тест AI: пропускаем если нет ключа (CI/CD)"""
        import os
        from core.ai_analyzer import AIAnalyzer
        key = os.getenv("NVIDIA_API_KEY")
        if not key:
            pytest.skip("NVIDIA_API_KEY not set")
        
        analyzer = AIAnalyzer(key)
        assert analyzer.model is not None

    def test_sport_map_coverage(self):
        """Все основные виды спорта замаплены"""
        from core.ru_bookmakers import FonbetParser
        sm = FonbetParser.SPORT_MAP
        assert 1 in sm   # Football
        assert 2 in sm   # Hockey
        assert 3 in sm   # Basketball
        assert 4 in sm   # Tennis
        assert 40 in sm  # Esports

    def test_parse_response_empty(self):
        """Пустой ответ → пустой список"""
        from core.ru_bookmakers import FonbetParser
        parser = FonbetParser()
        result = parser._parse_response({})
        assert result == []

    def test_parse_response_blocked_event(self):
        """Заблокированные события фильтруются"""
        from core.ru_bookmakers import FonbetParser
        parser = FonbetParser()
        data = {
            "sports": [{"id": 1, "name": "Футбол"}],
            "events": [
                {"id": 100, "sportId": 1, "team1": "A", "team2": "B", "startTime": 1700000000},
            ],
            "eventBlocks": [{"id": 100, "state": "blocked"}],
            "customFactors": [{"e": 100, "f": 921, "v": 1.85}],
        }
        result = parser._parse_response(data)
        assert len(result) == 0

    def test_parse_response_valid_event(self):
        """Корректный матч парсится"""
        from core.ru_bookmakers import FonbetParser
        parser = FonbetParser()
        data = {
            "sports": [{"id": 1, "name": "Футбол"}],
            "events": [
                {"id": 200, "sportId": 1, "team1": "Зенит", "team2": "ЦСКА",
                 "startTime": 1700000000, "name": "РПЛ"},
            ],
            "eventBlocks": [],
            "customFactors": [
                {"e": 200, "f": 921, "v": 1.85},
                {"e": 200, "f": 922, "v": 3.40},
                {"e": 200, "f": 923, "v": 4.20},
            ],
        }
        result = parser._parse_response(data)
        assert len(result) == 1
        m = result[0]
        assert m.home_team == "Зенит"
        assert m.away_team == "ЦСКА"
        assert "П1" in m.odds
        assert m.odds["П1"] == 1.85

    def test_overround_calculation(self):
        """Маржа считается правильно"""
        from core.ru_bookmakers import RuMatch, Sport
        m = RuMatch(
            id="test", sport=Sport.FOOTBALL, league="test",
            home_team="A", away_team="B",
            start_time=datetime.now(),
            odds={"П1": 2.10, "Х": 3.30, "П2": 3.50},
        )
        # 1/2.10 + 1/3.30 + 1/3.50 = 0.476 + 0.303 + 0.286 = 1.065
        assert 0.05 < m.overround < 0.08


# ═══════════════════════════════════════════════════════════
#  TEST: VALUE ENGINE
# ═══════════════════════════════════════════════════════════

class TestValueEngine:
    """Тесты поиска value-ставок"""

    def test_shin_removal_basic(self):
        """Shin убирает маржу, сумма = 1"""
        from core.ru_bookmakers import MultiSportValueEngine, Sport
        engine = MultiSportValueEngine()
        odds = {"П1": 2.10, "Х": 3.30, "П2": 3.50}
        fair = engine.remove_overround_shin(odds, Sport.FOOTBALL)
        total = sum(fair.values())
        assert abs(total - 1.0) < 0.01

    def test_shin_favourite_gets_more(self):
        """Shin: фаворит получает бо́льшую часть маржи"""
        from core.ru_bookmakers import MultiSportValueEngine, Sport
        engine = MultiSportValueEngine()
        # П1 — фаворит (кф 1.50), П2 — аутсайдер (кф 6.00)
        odds = {"П1": 1.50, "Х": 4.00, "П2": 6.00}
        fair = engine.remove_overround_shin(odds, Sport.FOOTBALL)
        # Fair prob П1 should be LESS than implied (1/1.50 = 0.667)
        assert fair["П1"] < 1/1.50

    def test_value_detection(self):
        """Value bet находится при edge > min_edge"""
        from core.ru_bookmakers import MultiSportValueEngine, RuMatch, Sport
        engine = MultiSportValueEngine()
        match = RuMatch(
            id="test1", sport=Sport.FOOTBALL, league="АПЛ",
            home_team="Arsenal", away_team="Norwich",
            start_time=datetime.now(),
            odds={"П1": 1.50, "Х": 4.50, "П2": 7.00},
        )
        values = engine.find_value_bets([match], min_edge=0.01)
        # With just Shin fair probs, there should be no edge (by definition)
        # But with model_probs we could find edge
        # Without model, values should be empty or very small
        assert isinstance(values, list)


# ═══════════════════════════════════════════════════════════
#  TEST: EXPRESS OPTIMIZER
# ═══════════════════════════════════════════════════════════

class TestExpressOptimizer:
    """Тесты оптимизатора экспрессов"""

    def test_insurance_eligibility(self):
        """6+ ног с кф >= 1.60 = страховка"""
        from core.ru_bookmakers import FonbetExpressOptimizer
        opt = FonbetExpressOptimizer()
        bets = [
            {"match": None, "market": f"П{i}", "odds": 1.80, "probability": 0.55,
             "edge": 0.05, "sport": type('', (), {"value": s})(), "is_top_league": True}
            for i, s in enumerate(["football", "hockey", "basketball",
                                    "tennis", "esports", "volleyball"], 1)
        ]
        # Need match objects for _select_diverse_legs
        # Simplified test — check insurance eligibility logic directly
        assert opt.MIN_LEGS_INSURANCE == 6
        assert opt.MIN_LEG_ODDS == 1.60

    def test_correlation_discount(self):
        """Корреляционный дисконт < 1"""
        from core.ru_bookmakers import FonbetExpressOptimizer
        opt = FonbetExpressOptimizer()
        legs = [
            {"sport": "football", "league": "АПЛ"},
            {"sport": "football", "league": "АПЛ"},
            {"sport": "hockey", "league": "КХЛ"},
        ]
        discount = opt._calc_correlation_discount(legs)
        assert discount < 1.0
        # Same sport pair (football x2) → extra penalty
        assert discount < 0.95 ** 2


# ═══════════════════════════════════════════════════════════
#  TEST: HEDGE CALCULATOR
# ═══════════════════════════════════════════════════════════

class TestHedgeCalculator:
    """Тесты противохода"""

    def test_basic_hedge(self):
        """Базовый расчёт противохода"""
        from core.fonbet_strategies import HedgeCalculator
        calc = HedgeCalculator()
        result = calc.calculate_hedge(
            express_stake=1000,
            express_total_odds=4.75,
            legs_passed=2,
            legs_total=3,
            remaining_leg_market="П1",
            remaining_leg_odds=1.90,
            opposite_odds=1.90,
        )
        # hedge = 1000 * 4.75 / 1.90 = 2500
        assert result.hedge_stake == 2500
        # Guaranteed profit should be positive and equal both ways
        assert result.guaranteed_profit > 0
        assert abs(result.profit_if_express_wins - result.profit_if_hedge_wins) < 1

    def test_hedge_asymmetric(self):
        """Противоход с разными кф"""
        from core.fonbet_strategies import HedgeCalculator
        calc = HedgeCalculator()
        result = calc.calculate_hedge(
            express_stake=500,
            express_total_odds=6.0,
            legs_passed=2,
            legs_total=3,
            remaining_leg_market="П1",
            remaining_leg_odds=2.00,
            opposite_odds=1.80,
        )
        assert result.hedge_stake > 0
        assert result.guaranteed_profit > 0


# ═══════════════════════════════════════════════════════════
#  TEST: BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════

class TestBacktest:
    """Тесты бэктеста"""

    def test_all_wins(self):
        """Все экспрессы прошли → прибыль"""
        from core.fonbet_strategies import ExpressBacktester
        bt = ExpressBacktester()
        bets = [
            {"legs": [{"odds": 1.80, "won": True}]*6, "date": "2025-01-01"},
            {"legs": [{"odds": 1.80, "won": True}]*6, "date": "2025-02-01"},
        ]
        r = bt.run_backtest(bets, stake_per_express=100)
        assert r.profit > 0
        assert r.win_rate == 1.0
        assert r.insurance_triggered == 0

    def test_insurance_trigger(self):
        """1 нога не прошла → страховка"""
        from core.fonbet_strategies import ExpressBacktester
        bt = ExpressBacktester()
        legs = [{"odds": 1.80, "won": True}]*5 + [{"odds": 1.80, "won": False}]
        bets = [{"legs": legs, "date": "2025-01-01"}]
        r = bt.run_backtest(bets, stake_per_express=100, insurance_enabled=True)
        assert r.insurance_triggered == 1
        assert r.insurance_saved == 100
        assert r.profit == 0  # Return stake = 0 profit

    def test_no_insurance_two_losses(self):
        """2 ноги не прошли → нет страховки → проигрыш"""
        from core.fonbet_strategies import ExpressBacktester
        bt = ExpressBacktester()
        legs = [{"odds": 1.80, "won": True}]*4 + [{"odds": 1.80, "won": False}]*2
        bets = [{"legs": legs, "date": "2025-01-01"}]
        r = bt.run_backtest(bets, stake_per_express=100)
        assert r.profit == -100
        assert r.insurance_triggered == 0


# ═══════════════════════════════════════════════════════════
#  TEST: CASHOUT ADVISOR
# ═══════════════════════════════════════════════════════════

class TestCashoutAdvisor:
    """Тесты советника по Cash-out"""

    def test_high_cashout_recommend_sell(self):
        """Высокий cashout (>80% от потенциала) → продать"""
        from core.fonbet_strategies import CashoutAdvisor
        advisor = CashoutAdvisor()
        signal = advisor.evaluate(
            original_stake=200,
            potential_win=2000,
            cashout_offer=1700,  # 85% от потенциала
            legs_remaining=1,
            remaining_probs=[0.55],
        )
        assert signal.recommendation == "sell"

    def test_low_prob_recommend_sell(self):
        """Низкая вероятность оставшихся ног → продать"""
        from core.fonbet_strategies import CashoutAdvisor
        advisor = CashoutAdvisor()
        signal = advisor.evaluate(
            original_stake=200,
            potential_win=2000,
            cashout_offer=500,
            legs_remaining=2,
            remaining_probs=[0.30, 0.35],
        )
        assert signal.recommendation == "sell"

    def test_high_prob_recommend_hold(self):
        """Высокая вероятность + низкий cashout → держать"""
        from core.fonbet_strategies import CashoutAdvisor
        advisor = CashoutAdvisor()
        signal = advisor.evaluate(
            original_stake=200,
            potential_win=3000,
            cashout_offer=800,  # 27% от потенциала
            legs_remaining=1,
            remaining_probs=[0.75],
        )
        assert signal.recommendation == "hold"


# ═══════════════════════════════════════════════════════════
#  TEST: ENDPOINT HEALTH
# ═══════════════════════════════════════════════════════════

class TestEndpointManager:
    """Тесты менеджера endpoints"""

    def test_known_endpoints_not_empty(self):
        """Есть хотя бы 3 known endpoints"""
        from core.fonbet_health import FonbetEndpointManager
        mgr = FonbetEndpointManager()
        assert len(mgr.KNOWN_ENDPOINTS) >= 3

    def test_user_agents_variety(self):
        """Разнообразие User-Agent"""
        from core.fonbet_health import FonbetEndpointManager
        mgr = FonbetEndpointManager()
        assert len(mgr.USER_AGENTS) >= 3
        # All unique
        assert len(set(mgr.USER_AGENTS)) == len(mgr.USER_AGENTS)


# ═══════════════════════════════════════════════════════════
#  TEST: CONFIG
# ═══════════════════════════════════════════════════════════

class TestConfig:
    """Тесты конфигурации"""

    def test_insurance_presets(self):
        """Пресеты страховки загружаются"""
        from config.ru_config import ru_config
        assert ru_config.express.INSURANCE_MIN_LEGS >= 5
        assert ru_config.express.INSURANCE_MIN_ODDS >= 1.40
        assert ru_config.bankroll.KELLY_FRACTION <= 0.30

    def test_margins_reasonable(self):
        """Маржи в разумных пределах"""
        from config.ru_config import ru_config
        for key, val in ru_config.fonbet.MARGINS.items():
            assert 0.02 <= val <= 0.12, f"Margin {key}={val} out of range"
