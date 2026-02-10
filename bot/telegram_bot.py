"""
=============================================================================
 BETTING ASSISTANT — TELEGRAM BOT
 Aiogram 3.x бот с полным набором команд
=============================================================================

 Команды:
   /start     — Приветствие + меню
   /scan      — Запустить сканирование (только админ)
   /signals   — Показать последние сигналы
   /express   — Показать текущие экспрессы
   /system    — Показать текущие системы
   /bankroll  — Статистика банкролла
   /settings  — Настройки (edge, Kelly fraction, лиги)
   /stop      — Остановить мониторинг
   /resume    — Возобновить мониторинг
   /help      — Справка
=============================================================================
"""
import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config.settings import betting_config, tg_config
from core.models import ExpressBet, SystemBet, ValueSignal

logger = logging.getLogger(__name__)

router = Router()


class TelegramNotifier:
    """
    Модуль отправки уведомлений в Telegram.
    Используется SignalGenerator для push-уведомлений.
    """

    def __init__(self, bot: Bot, channel_id: str = None):
        self.bot = bot
        self.channel_id = channel_id or tg_config.CHANNEL_ID

    async def send_signal(self, signal: ValueSignal):
        """Отправить сигнал на одиночную ставку"""
        if not self.channel_id:
            return
        try:
            msg = signal.to_telegram_message()
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Поставил", callback_data=f"bet_placed:{signal.id}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Пропустил", callback_data=f"bet_skipped:{signal.id}"
                    ),
                ],
            ])
            await self.bot.send_message(
                self.channel_id, msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Failed to send signal: {e}")

    async def send_express(self, express: ExpressBet):
        """Отправить экспресс-сигнал"""
        if not self.channel_id:
            return
        try:
            msg = express.to_telegram_message()
            await self.bot.send_message(
                self.channel_id, msg, parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send express: {e}")

    async def send_system(self, system: SystemBet):
        """Отправить систему"""
        if not self.channel_id:
            return
        try:
            msg = system.to_telegram_message()
            await self.bot.send_message(
                self.channel_id, msg, parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send system: {e}")

    async def send_text(self, text: str):
        """Отправить произвольный текст"""
        if not self.channel_id:
            return
        await self.bot.send_message(
            self.channel_id, text, parse_mode=ParseMode.HTML
        )


# ===================================================================
#  ОБРАБОТЧИКИ КОМАНД
# ===================================================================

# Глобальные ссылки (устанавливаются при запуске)
_signal_generator = None
_bankroll_manager = None


def setup_handlers(signal_generator, bankroll_manager):
    global _signal_generator, _bankroll_manager
    _signal_generator = signal_generator
    _bankroll_manager = bankroll_manager


def is_admin(user_id: int) -> bool:
    return user_id in tg_config.ADMIN_IDS


@router.message(CommandStart())
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔍 Сканировать", callback_data="action:scan"),
            InlineKeyboardButton(text="📊 Банкролл", callback_data="action:bankroll"),
        ],
        [
            InlineKeyboardButton(text="🎯 Сигналы", callback_data="action:signals"),
            InlineKeyboardButton(text="🔥 Экспрессы", callback_data="action:express"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="action:settings"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="action:help"),
        ],
    ])

    await message.answer(
        "🤖 <b>Betting Assistant</b>\n\n"
        "AI-ассистент для поиска Value Bets,\n"
        "формирования экспрессов и систем.\n\n"
        "Выберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


@router.message(Command("scan"))
async def cmd_scan(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администраторов")
        return

    if not _signal_generator:
        await message.answer("⚠️ Генератор сигналов не инициализирован")
        return

    await message.answer("🔍 Сканирование запущено...")
    try:
        result = await _signal_generator.run_scan()
        singles = result.get("singles", [])
        expresses = result.get("expresses", [])
        system = result.get("system")

        summary = (
            f"✅ <b>Сканирование завершено</b>\n\n"
            f"📊 Матчей просканировано: {result['total_matches_scanned']}\n"
            f"🎯 Одиночных сигналов: {len(singles)}\n"
            f"🔥 Экспрессов: {len(expresses)}\n"
            f"🎰 Система: {'Да' if system else 'Нет'}\n\n"
            f"📈 {result['api_usage']}"
        )
        await message.answer(summary, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")


@router.message(Command("bankroll"))
async def cmd_bankroll(message: Message):
    if _bankroll_manager:
        await message.answer(
            _bankroll_manager.stats_telegram_message(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer("⚠️ Менеджер банкролла не инициализирован")


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администраторов")
        return
    if _bankroll_manager:
        _bankroll_manager._is_stopped = True
        _bankroll_manager._stop_reason = "Manual stop by admin"
        await message.answer("⛔ Мониторинг остановлен")


@router.message(Command("resume"))
async def cmd_resume(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Только для администраторов")
        return
    if _bankroll_manager:
        _bankroll_manager.reset_stop_loss()
        await message.answer("✅ Мониторинг возобновлен")


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    cfg = betting_config
    await message.answer(
        f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
        f"📊 Min Edge: {cfg.MIN_VALUE_EDGE:.0%}\n"
        f"📊 Max Edge: {cfg.MAX_VALUE_EDGE:.0%}\n"
        f"🎲 Min Odds: {cfg.MIN_ODDS}\n"
        f"🎲 Max Odds: {cfg.MAX_ODDS}\n"
        f"💰 Kelly Fraction: {cfg.KELLY_FRACTION}\n"
        f"💰 Max Bet: {cfg.MAX_BET_PERCENT:.0%} банка\n"
        f"💰 Max Express Bet: {cfg.MAX_EXPRESS_BET_PERCENT:.0%} банка\n"
        f"⛔ Daily Stop-loss: {cfg.MAX_DAILY_LOSS_PERCENT:.0%}\n"
        f"⛔ Weekly Stop-loss: {cfg.MAX_WEEKLY_LOSS_PERCENT:.0%}\n"
        f"🏟 Лиги: {len(cfg.SPORTS)} шт.\n"
        f"⏱ Интервал опроса: {cfg.ODDS_POLL_INTERVAL}с\n",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "❓ <b>СПРАВКА</b>\n\n"
        "<b>Команды:</b>\n"
        "/scan — Сканировать рынок\n"
        "/bankroll — Статистика банкролла\n"
        "/settings — Текущие настройки\n"
        "/stop — Остановить мониторинг\n"
        "/resume — Возобновить\n"
        "/help — Эта справка\n\n"
        "<b>Что такое Value Bet?</b>\n"
        "Ставка с положительным мат. ожиданием.\n"
        "Edge = P_модели × Кф_БК - 1\n"
        "Если Edge > 2% → сигнал на ставку.\n\n"
        "<b>Экспресс (AI):</b> Умный подбор 2-5 событий с анализом.\n"
        "Бот объясняет причину выбора каждого исхода (P, Edge).\n"
        "<b>Система:</b> Комбинация экспрессов (напр. 3 из 4).\n",
        parse_mode=ParseMode.HTML,
    )


# ===================================================================
#  CALLBACK HANDLERS
# ===================================================================

@router.callback_query(F.data.startswith("action:"))
async def handle_action(callback: CallbackQuery):
    action = callback.data.split(":")[1]

    if action == "scan":
        await callback.message.answer("🔍 Используйте /scan для сканирования")
    elif action == "bankroll":
        if _bankroll_manager:
            await callback.message.answer(
                _bankroll_manager.stats_telegram_message(),
                parse_mode=ParseMode.HTML,
            )
    elif action == "settings":
        await cmd_settings(callback.message)
    elif action == "help":
        await cmd_help(callback.message)
    elif action == "signals":
        await callback.message.answer(
            "🎯 Последние сигналы будут отправлены после /scan"
        )
    elif action == "express":
        await callback.message.answer(
            "🔥 Экспрессы формируются автоматически после /scan"
        )

    await callback.answer()


@router.callback_query(F.data.startswith("bet_placed:"))
async def handle_bet_placed(callback: CallbackQuery):
    signal_id = callback.data.split(":")[1]
    logger.info(f"👤 User confirmed bet {signal_id}")
    
    # Try to find signal in generator memory
    signal = None
    if _signal_generator:
        # Check active scan results
        # Note: _signals_today might be cleared or we might need access to last scan results
        # We can try to search in _signals_today which should accumulate active signals
        for s in _signal_generator._signals_today:
             if s.id == signal_id:
                 signal = s
                 break

    if signal and _bankroll_manager:
        info = f"{signal.match.home_team} vs {signal.match.away_team} ({signal.outcome.value})"
        _bankroll_manager.record_bet(signal.id, "single", signal.stake_amount, signal.bookmaker_odds, match_info=info)
        await callback.message.answer(f"✅ Ставка #{signal_id} ({signal.match.home_team} vs {signal.match.away_team}) записана в банкролл! Сумма: {signal.stake_amount}₽")
        await callback.answer()
    else:
        await callback.answer(f"⚠️ Сигнал #{signal_id} не найден в памяти (возможно устарел).")


# ===================================================================
#  ЗАПУСК БОТА
# ===================================================================

async def start_bot(signal_generator=None, bankroll_manager=None):
    """Основная точка входа для Telegram бота"""
    bot = Bot(token=tg_config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Настраиваем зависимости
    if signal_generator and bankroll_manager:
        setup_handlers(signal_generator, bankroll_manager)

        # Настраиваем нотификатор
        notifier = TelegramNotifier(bot, tg_config.CHANNEL_ID)
        signal_generator.notifier = notifier

    logger.info("🤖 Telegram bot starting...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
