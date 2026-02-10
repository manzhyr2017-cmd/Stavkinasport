import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_BOT_TOKEN")

async def main():
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not found in .env")
        return

    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message()
    async def get_id(message: types.Message):
        chat_id = message.chat.id
        print(f"✅ Chat ID: {chat_id}")
        await message.answer(f"ID этого чата: {chat_id}")

    print("🤖 Бот запущен. Добавьте его в вашу группу и напишите любое сообщение.")
    print("После этого ID группы появится здесь.")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
