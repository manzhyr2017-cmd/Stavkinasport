"""
=============================================================================
 BETTING ASSISTANT V2 — ML TRAINER
 Pipeline: Fetch Data → Fit Models → Calibrate
=============================================================================
"""
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
import numpy as np
from sqlalchemy import select

from config.settings import model_config, db_config
from core.prediction_models import DixonColesModel, EloRatingSystem
from data.database import MatchHistory, AsyncSessionLocal

logger = logging.getLogger(__name__)

class ModelTrainer:
    def __init__(self):
        self.dc = DixonColesModel()
        self.elo = EloRatingSystem()
        from core.prediction_models import ProbabilityCalibration
        self.calibrator = ProbabilityCalibration()
        
        try:
            from core.ml_pipeline import FeatureEngineer, CatBoostPipeline
            self.feature_engineer = FeatureEngineer()
            self.catboost_pipeline = CatBoostPipeline()
        except ImportError:
            self.feature_engineer = None
            self.catboost_pipeline = None

    async def fetch_training_data(self) -> List[dict]:
        """Fetch historical matches from database with all fields"""
        async with AsyncSessionLocal() as session:
            stmt = select(MatchHistory).order_by(MatchHistory.date)
            result = await session.execute(stmt)
            matches = result.scalars().all()
            
            if not matches:
                logger.info("Database empty. Starting history import...")
                from data.history_importer import run_import
                await run_import()
                result = await session.execute(stmt)
                matches = result.scalars().all()

            data = []
            for m in matches:
                data.append({
                    "home": m.home_team,
                    "away": m.away_team,
                    "home_goals": m.home_goals,
                    "away_goals": m.away_goals,
                    "home_xg": m.home_xg,
                    "away_xg": m.away_xg,
                    "pinnacle_home": m.pinnacle_home,
                    "pinnacle_draw": m.pinnacle_draw,
                    "pinnacle_away": m.pinnacle_away,
                    "date": m.date,
                    "league": m.league
                })
            return data

    def prepare_features(self, matches: List[dict]) -> pd.DataFrame:
        """Create 48 features using Stavki3 FeatureEngineer"""
        if not self.feature_engineer:
            logger.error("FeatureEngineer not available")
            return pd.DataFrame()

        df_history = pd.DataFrame(matches)
        
        features_list = []
        # We need Elo and DC ratings as inputs for feature engineering
        # For simplicity, we fit on the whole set here, but FeatureEngineer handles historical context
        logger.info("Pre-fitting DC & Elo for feature extraction...")
        self.dc.fit(matches)
        self.elo.fit(matches)

        dc_params = self.dc.params
        elo_ratings = self.elo.ratings

        for i, m in enumerate(matches):
            odds = None
            if m["pinnacle_home"]:
                odds = {
                    "home": m["pinnacle_home"],
                    "draw": m["pinnacle_draw"],
                    "away": m["pinnacle_away"]
                }
            
            # FeatureEngineer uses history BEFORE the match date
            feats = self.feature_engineer.compute_features(
                m, df_history.iloc[:i], elo_ratings, dc_params, odds
            )
            
            # Target: 0=Home, 1=Draw, 2=Away
            if m["home_goals"] > m["away_goals"]:
                target = 0
            elif m["home_goals"] == m["away_goals"]:
                target = 1
            else:
                target = 2
            
            feats["target"] = target
            features_list.append(feats)
            
        return pd.DataFrame(features_list)

    async def run_full_pipeline(self):
        """Execute full training lifecycle with advanced ML"""
        matches = await self.fetch_training_data()
        if not matches:
            logger.error("No historical data found")
            return
            
        logger.info(f"Found {len(matches)} matches. fitting DC & Elo...")
        self.dc.fit(matches)
        self.elo.fit(matches)
        
        if model_config.USE_CATBOOST and self.catboost_pipeline:
            logger.info("Starting advanced 48-feature training...")
            df = self.prepare_features(matches)
            
            X = df.drop(columns=["target"])
            y = df["target"].values
            
            metrics = self.catboost_pipeline.train(X, y)
            self.catboost_pipeline.save()
            logger.info(f"Training metrics: {metrics}")
            
        # Calibration (simple version using DC output)
        logger.info("Fitting probability calibrator...")
        y_true, y_prob = [], []
        for m in matches:
            pred = self.dc.predict_probabilities(m["home"], m["away"])
            if pred:
                y_prob.append([pred["home"], pred["draw"], pred["away"]])
                if m["home_goals"] > m["away_goals"]: y_true.append(0)
                elif m["home_goals"] == m["away_goals"]: y_true.append(1)
                else: y_true.append(2)
        
        if len(y_true) >= 200:
            self.calibrator.fit(np.array(y_true), np.array(y_prob))
            self.calibrator.save()

        self.dc.save()
        self.elo.save()
        logger.info("✅ Advanced training pipeline completed")

# Singleton
trainer = ModelTrainer()
