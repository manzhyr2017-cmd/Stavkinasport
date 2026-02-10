import asyncio
import os
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()

import pytest

@pytest.mark.asyncio
async def test_msg():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHANNEL_ID")
    if not token or not chat_id:
        if os.getenv("GITHUB_ACTIONS") == "true":
            pytest.skip("Telegram credentials missing in CI")
        print("❌ Missing token or chat_id in .env")
        return
    
    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id, "🚀 <b>Система Stavkinasport V2 подключена!</b>\n\nМодели обучены, NLP мониторинг расширен. Бот готов к поиску сигналов.", parse_mode="HTML")
        print("✅ Message sent successfully!")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(test_msg())
