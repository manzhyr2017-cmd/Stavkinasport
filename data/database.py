"""
=============================================================================
 BETTING ASSISTANT V2 — DATABASE LAYER
 PostgreSQL (SQLAlchemy Async) + Redis
=============================================================================
"""
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Column, DateTime, Float, String, Boolean, JSON, ForeignKey
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from config.settings import db_config

logger = logging.getLogger(__name__)

# --- Models ---

class Base(DeclarativeBase):
    pass

class MatchHistory(Base):
    __tablename__ = "match_history"

    id = Column(String, primary_key=True)
    sport = Column(String)
    league = Column(String)
    home_team = Column(String)
    away_team = Column(String)
    home_goals = Column(BigInteger)
    away_goals = Column(BigInteger)
    home_xg = Column(Float, nullable=True)
    away_xg = Column(Float, nullable=True)
    pinnacle_home = Column(Float, nullable=True)
    pinnacle_draw = Column(Float, nullable=True)
    pinnacle_away = Column(Float, nullable=True)
    date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class SignalLog(Base):
    __tablename__ = "signal_logs"

    id = Column(String, primary_key=True)
    match_id = Column(String)
    sport = Column(String)
    league = Column(String)
    match_name = Column(String)  # Team A vs Team B
    market = Column(String)
    outcome = Column(String)
    model_probability = Column(Float)
    bookmaker_odds = Column(Float)
    bookmaker_name = Column(String)
    edge = Column(Float)
    stake_amount = Column(Float)
    status = Column(String)      # pending, won, lost, void
    result_score = Column(String, nullable=True) # "2-1"
    closing_odds = Column(Float, nullable=True)
    ai_analysis = Column(String, nullable=True)
    metadata = Column(JSON, nullable=True)        # Store xG stats, news snippets at bet time
    created_at = Column(DateTime, default=datetime.utcnow)

class BankrollSnapshot(Base):
    __tablename__ = "bankroll_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    balance = Column(Float)
    equity = Column(Float)  # balance + floating stakes
    daily_pnl = Column(Float)

# --- Engine & Session ---

engine = create_async_engine(db_config.POSTGRES_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    """Initializes the database, creating tables if they don't exist."""
    try:
        async with engine.begin() as conn:
            # Note: In a real production environment, use Alembic for migrations
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created/verified")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
