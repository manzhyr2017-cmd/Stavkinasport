"""
=============================================================================
 BETTING ASSISTANT V2 — VERIFICATION SCRIPT
 Tests core logic with mocked data
=============================================================================
"""
import asyncio
import logging
from datetime import datetime

from core.prediction_models import DixonColesModel, EloRatingSystem, EnsemblePredictor
from core.value_engine import ValueBettingEngine
from core.models import Match, BookmakerOdds, Market, BetOutcome

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_logic():
    # 1. Mock Dixon-Coles
    dc = DixonColesModel()
    dc.params = {
        "attack": {"HomeTeam": 1.2, "AwayTeam": 0.8},
        "defence": {"HomeTeam": 0.9, "AwayTeam": 1.1},
        "gamma": 1.25,
        "rho": -0.03
    }
    dc.teams = ["HomeTeam", "AwayTeam"]
    dc._fitted = True
    
    # 2. Mock Elo
    elo = EloRatingSystem()
    elo.ratings = {"HomeTeam": 1600, "AwayTeam": 1450}
    
    # 3. Ensemble
    predictor = EnsemblePredictor(dc, elo)
    
    # 4. Engine
    engine = ValueBettingEngine(predictor)
    
    # 5. Mock Match
    match = Match(
        id="test_match",
        sport="soccer_epl",
        league="EPL",
        home_team="HomeTeam",
        away_team="AwayTeam",
        commence_time=datetime.utcnow(),
        bookmaker_odds=[
            BookmakerOdds(
                bookmaker="Pinnacle",
                market=Market.H2H,
                outcomes={"home": 2.2, "draw": 3.4, "away": 3.6}
            ),
            BookmakerOdds(
                bookmaker="Ref_BK",
                market=Market.H2H,
                outcomes={"home": 2.5, "draw": 3.2, "away": 3.8} # High edge here
            )
        ]
    )
    
    logger.info("Running value detection...")
    signals = engine.find_value_bets([match])
    
    for s in signals:
        logger.info(f"Signal found: {s.outcome} @ {s.bookmaker_odds} (BK: {s.bookmaker_name})")
        logger.info(f"Edge: {s.edge:+.1%}")
    
    if signals:
        logger.info("✅ Core logic verified!")
    else:
        logger.warning("❌ No signals found in mock data.")

if __name__ == "__main__":
    verify_logic()
