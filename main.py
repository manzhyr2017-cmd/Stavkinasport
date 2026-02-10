"""
=============================================================================
 BETTING ASSISTANT V2 — MAIN
 
 python main.py              → Bot + monitoring
 python main.py --scan-once  → Single scan
 python main.py --bot-only   → Bot without monitoring
 python main.py --train      → Train Dixon-Coles + Elo on history
=============================================================================
"""
import argparse
import asyncio
import logging
import sys

from config.settings import api_config, tg_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("betting_v2.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def validate():
    errors = []
    if not api_config.ODDS_API_KEY:
        errors.append("ODDS_API_KEY — get free key at https://the-odds-api.com")
    if not tg_config.BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN — create via @BotFather")
    if errors:
        for e in errors:
            logger.error(f"❌ Missing: {e}")
        logger.error("Create .env from .env.example and fill in your keys")
        return False
    return True


async def scan_once():
    """One-time scan for testing"""
    from core.bankroll import BankrollManager
    from core.signal_generator import SignalGenerator
    from data.odds_fetcher import OddsDataFetcher

    fetcher = OddsDataFetcher()
    bankroll = BankrollManager()
    gen = SignalGenerator(fetcher, bankroll)

    try:
        result = await gen.run_scan()
        singles = result.get("singles", [])
        expresses = result.get("expresses", [])
        systems = result.get("systems", [])

        print("\n" + "=" * 65)
        print(f"  📊 SCAN RESULTS")
        print(f"  Matches: {result['matches_scanned']}")
        print(f"  Singles: {len(singles)} | Expresses: {len(expresses)} "
              f"| Systems: {len(systems)}")
        print("=" * 65)

        for i, s in enumerate(singles[:10], 1):
            m = s.match
            if m:
                sharp = "✓sharp" if s.sharp_agrees else ""
                print(
                    f"\n  {i}. {m.home_team} vs {m.away_team} ({m.league})\n"
                    f"     {s.outcome.value.upper()} @ {s.bookmaker_odds:.2f} "
                    f"({s.bookmaker_name})\n"
                    f"     P={s.model_probability:.1%} Edge={s.edge:+.1%} "
                    f"Stake={s.stake_amount:.2f}₽ "
                    f"{s.confidence_level.value} {sharp}"
                )

        if expresses:
            print(f"\n{'─' * 65}")
            print("  🔥 EXPRESSES:")
            for i, e in enumerate(expresses[:5], 1):
                print(
                    f"  #{i}: {len(e.legs)} legs | Odds={e.total_odds:.2f} | "
                    f"EV={e.adjusted_ev:+.1%} | "
                    f"Corr={e.correlation_discount:.0%} | "
                    f"Stake={e.stake_amount:.2f}₽"
                )
                if e.analysis:
                    print(f"  📝 Analysis: {e.analysis.replace(chr(10), ' | ')}")

        if systems:
            print(f"\n{'─' * 65}")
            print("  🎰 SYSTEMS:")
            for sys in systems[:3]:
                print(
                    f"  {sys.system_size}/{sys.total_legs}: "
                    f"{sys.num_combinations} combos | "
                    f"Avg P={sys.avg_leg_prob:.0%} | "
                    f"Total={sys.total_stake:.2f}₽"
                )

        stats = result["stats"]
        print(f"\n{'─' * 65}")
        print(f"  💰 Bankroll: {stats['bankroll']}₽ | "
              f"Kelly: {stats['kelly_fraction']:.0%}")
        print(f"  📊 {result['api_usage']}")
        print("=" * 65)

    finally:
        await fetcher.close()


async def train():
    """Train/Retrain models on historical data"""
    from core.trainer import trainer
    from data.database import init_db

    await init_db()
    logger.info("🛠️ Starting training pipeline...")
    await trainer.run_full_pipeline()
    logger.info("✅ Models trained and saved")


async def run_full(start_dashboard: bool = True):
    """Bot + monitoring + dashboard (optional)"""
    from bot.telegram_bot import start_bot
    from bot.dashboard import create_app
    from core.bankroll import BankrollManager
    from core.signal_generator import SignalGenerator
    from data.odds_fetcher import OddsDataFetcher
    from data.database import init_db
    import uvicorn

    await init_db()
    fetcher = OddsDataFetcher()
    bankroll = BankrollManager()
    gen = SignalGenerator(fetcher, bankroll)
    
    tasks = [
        start_bot(gen, bankroll),
        gen.run_continuous(),
    ]

    if start_dashboard:
        logger.info("🚀 Starting Dashboard on http://localhost:8081")
        app = create_app(gen, bankroll)
        config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
        server = uvicorn.Server(config)
        tasks.append(server.serve())

    await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser(description="Betting Assistant V2")
    parser.add_argument("--scan-once", action="store_true",
                        help="Single scan and exit")
    parser.add_argument("--bot-only", action="store_true",
                        help="Run Telegram bot only")
    parser.add_argument("--train", action="store_true",
                        help="Train models on history")
    parser.add_argument("--dashboard", action="store_true",
                        help="Run monitoring dashboard (standalone)")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="Disable dashboard in full run")
    args = parser.parse_args()

    if not validate() and not args.scan_once and not args.train and not args.dashboard:
        sys.exit(1)

    if args.scan_once:
        asyncio.run(scan_once())
    elif args.train:
        asyncio.run(train())
    elif args.dashboard:
        # Standalone dashboard (mostly for dev/debug)
        import uvicorn
        from bot.dashboard import create_app
        from core.signal_generator import SignalGenerator
        from core.bankroll import BankrollManager
        
        bankroll = BankrollManager()
        gen = SignalGenerator(bankroll=bankroll)
        app = create_app(gen, bankroll)
        uvicorn.run(app, host="0.0.0.0", port=8081)
    elif args.bot_only:
        # Run full WITHOUT dashboard
        asyncio.run(run_full(start_dashboard=False))
    else:
        # Run FULL (Bot + Scan + Dashboard)
        asyncio.run(run_full(start_dashboard=not args.no_dashboard))


if __name__ == "__main__":
    main()
