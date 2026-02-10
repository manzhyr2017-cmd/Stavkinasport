
import asyncio
import logging
import sys
import os
from datetime import datetime, timedelta, timezone

# Add root to sys.path
sys.path.append(os.getcwd())

from core.models import Match, ValueSignal, Market, BetOutcome, ConfidenceLevel
from core.ai_analyzer import AIAnalyzer
from core.bravo_api import BravoNewsFetcher
from data.database import init_db, AsyncSessionLocal, SignalLog
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_enrichment_and_db():
    print("\n🚀 STARTING INTEGRATION TEST (AI + DB + DASHBOARD)\n" + "="*50)
    
    # 1. Initialize DB
    await init_db()
    
    # 2. Mock a High Edge Signal
    mock_match = Match(
        id="test_ai_001",
        sport="soccer",
        league="Premier League",
        home_team="Manchester City",
        away_team="Arsenal",
        commence_time=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    )
    
    signal = ValueSignal(
        match=mock_match,
        market=Market.H2H,
        outcome=BetOutcome.HOME,
        model_probability=0.65,
        bookmaker_odds=1.85,
        bookmaker_name="Fonbet",
        edge=0.20,
        stake_amount=500.0,
        confidence_level=ConfidenceLevel.HIGH
    )
    
    # 3. AI Enrichment
    print("\n[1] Testing AI Analysis Flow...")
    news_fetcher = BravoNewsFetcher()
    news = await news_fetcher.get_latest_news("Manchester City vs Arsenal injuries")
    
    analyzer = AIAnalyzer()
    analysis = await analyzer.generate_analysis(signal, news)
    signal.analysis = analysis
    
    print(f"✅ AI Analysis Generated:\n{analysis}")
    
    # 4. DB Persistence
    print("\n[2] Testing Database Persistence...")
    async with AsyncSessionLocal() as session:
        log = SignalLog(
            id=signal.id,
            match_id=signal.match.id,
            sport=signal.match.sport,
            league=signal.match.league,
            match_name=f"{signal.match.home_team} vs {signal.match.away_team}",
            market=signal.market.value,
            outcome=signal.outcome.value,
            model_probability=signal.model_probability,
            bookmaker_odds=signal.bookmaker_odds,
            bookmaker_name=signal.bookmaker_name,
            edge=signal.edge,
            stake_amount=signal.stake_amount,
            status=signal.status.value,
            ai_analysis=signal.analysis,
            meta_payload={},
            created_at=signal.created_at
        )
        session.add(log)
        await session.commit()
        print(f"✅ Signal {signal.id} saved to DB.")

        # Verify retrieval
        stmt = select(SignalLog).where(SignalLog.id == signal.id)
        res = await session.execute(stmt)
        saved = res.scalar_one_or_none()
        if saved and saved.ai_analysis:
            print(f"✅ DB Verification: Retrieval successful and AI analysis present.")
        else:
            print(f"❌ DB Verification FAILED.")

    # 5. Dashboard API Health Check (if running)
    print("\n[3] Checking Dashboard API Ready state...")
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # Attempt to connect to local dashboard if running
            try:
                resp = await client.get("http://localhost:8000/api/health", timeout=2.0)
                if resp.status_code == 200:
                    print("✅ Dashboard API is ACTIVE and reachable.")
                else:
                    print(f"⚠️ Dashboard API returned {resp.status_code}. It might be running but unhealthy.")
            except Exception:
                print("ℹ️ Dashboard API not running locally. (Expected if uvicorn wasn't started)")
    except ImportError:
        print("ℹ️ httpx not installed, skipping API connectivity test.")

    print("\n" + "="*50 + "\n🚀 TEST COMPLETED")

if __name__ == "__main__":
    asyncio.run(test_enrichment_and_db())
