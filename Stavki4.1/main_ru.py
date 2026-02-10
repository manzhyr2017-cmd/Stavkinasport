"""
=============================================================================
 BETTING ASSISTANT — RUSSIAN MODE (main_ru.py)
 
 python main_ru.py                → Фонбет сканирование + Telegram
 python main_ru.py --scan-once    → Одно сканирование
 python main_ru.py --express-only → Только экспрессы со страховкой
 python main_ru.py --live         → Включить лайв
=============================================================================
"""
import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("betting_ru.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def scan_once(include_live: bool = False, express_only: bool = False):
    """Одно сканирование Фонбет"""
    from core.ru_bookmakers import RuBettingAssistant

    assistant = RuBettingAssistant()
    try:
        result = await assistant.scan(include_live=include_live)

        print("\n" + "=" * 65)
        print("  🤖 СКАНИРОВАНИЕ ФОНБЕТ")
        print(f"  📊 Матчей: {result['matches']}")
        print(f"  🎯 Валуев: {len(result['value_bets'])}")
        print(f"  🔥 Экспрессов: {len(result['expresses'])}")
        print(f"  🛡️ Со страховкой: {len(result['insurance_expresses'])}")
        print("=" * 65)

        # По спортам
        for sport, count in sorted(
            result.get("by_sport", {}).items(), key=lambda x: -x[1]
        ):
            print(f"  {sport}: {count} матчей")

        if not express_only:
            # Топ value bets
            print(f"\n{'─' * 65}")
            print("  🎯 ТОП ВАЛУИ:")
            for i, vb in enumerate(result["value_bets"][:10], 1):
                m = vb["match"]
                top = "⭐" if vb["is_top_league"] else ""
                print(
                    f"\n  {i}. {m.display_name} {top}\n"
                    f"     {vb['market']} @ {vb['odds']:.2f} "
                    f"(P={vb['probability']:.0%}, edge={vb['edge']:+.1%})\n"
                    f"     {m.sport.value} | {m.league}"
                )

        # Экспрессы
        if result["insurance_expresses"]:
            print(f"\n{'─' * 65}")
            print("  🛡️ ЭКСПРЕССЫ СО СТРАХОВКОЙ:")
            for i, e in enumerate(result["insurance_expresses"][:3], 1):
                print(f"\n  #{i}: {e.num_legs} ног | Кф={e.total_odds:.2f} | "
                      f"EV={e.ev:+.1%} | EV+страх={e.effective_ev:+.1%}")
                for j, leg in enumerate(e.legs, 1):
                    m = leg["match"]
                    print(f"    {j}. {m.display_name} — "
                          f"{leg['market']} @ {leg['odds']:.2f} "
                          f"({m.sport.value})")

        if result["expresses"]:
            print(f"\n{'─' * 65}")
            print("  🔥 ВСЕ ЭКСПРЕССЫ:")
            for i, e in enumerate(result["expresses"][:5], 1):
                ins = "🛡️" if e.insurance_eligible else ""
                print(f"  #{i}: {e.num_legs} ног | Кф={e.total_odds:.2f} | "
                      f"EV={e.ev:+.1%} {ins}")

        print("\n" + "=" * 65)

    finally:
        await assistant.close()


async def run_continuous(include_live: bool = False):
    """Непрерывное сканирование с отправкой в Telegram"""
    from core.ru_bookmakers import RuBettingAssistant
    from config.ru_config import ru_config

    assistant = RuBettingAssistant()
    interval = ru_config.fonbet.PREMATCH_INTERVAL

    logger.info(f"Starting continuous scan, interval={interval}s")

    try:
        while True:
            try:
                result = await assistant.scan(include_live=include_live)
                report = await assistant.format_telegram_report(result)
                logger.info(f"Scan complete: {result['matches']} matches, "
                            f"{len(result['value_bets'])} values, "
                            f"{len(result['expresses'])} expresses")

                # TODO: отправка в Telegram
                # await bot.send_message(chat_id, report, parse_mode="Markdown")

            except Exception as e:
                logger.error(f"Scan error: {e}")

            await asyncio.sleep(interval)
    finally:
        await assistant.close()


def main():
    parser = argparse.ArgumentParser(description="Betting Assistant — Russian Mode")
    parser.add_argument("--scan-once", action="store_true",
                        help="Одно сканирование и выход")
    parser.add_argument("--express-only", action="store_true",
                        help="Только экспрессы")
    parser.add_argument("--live", action="store_true",
                        help="Включить лайв")
    args = parser.parse_args()

    if args.scan_once or args.express_only:
        asyncio.run(scan_once(args.live, args.express_only))
    else:
        asyncio.run(run_continuous(args.live))


if __name__ == "__main__":
    main()
