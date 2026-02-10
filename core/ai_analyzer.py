"""
=============================================================================
 AI ANALYZER (NVIDIA NIM)
 
 reasoning engine для обоснования ставок на основе новостей и статистики.
=============================================================================
"""
import aiohttp
import logging
import json
from typing import List, Optional
from core.models import ValueSignal
from config.settings import api_config

logger = logging.getLogger(__name__)

class AIAnalyzer:
    """
    Генерирует детальный анализ ставки, объединяя xG, новости и доменити.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or api_config.NVIDIA_API_KEY
        self.base_url = "https://integrate.api.nvidia.com/v1/chat/completions"
        self.model = "meta/llama3-70b-instruct"

    async def generate_analysis(self, signal: ValueSignal, news: List[dict] = None) -> str:
        """
        Создаёт текстовое обоснование для сигнала.
        """
        if not self.api_key:
            return "Аналитическое обоснование временно недоступно (ключ не найден)."

        m = signal.match
        if not m:
            return "Недостаточно данных для анализа матча."

        # Подготовка контекста новостей
        news_text = "Свежих новостей по командам не найдено."
        if news:
            news_items = [f"- {n['title']} ({n['source']})" for n in news[:3]]
            news_text = "\n".join(news_items)

        # Промпт для NVIDIA NIM
        prompt = (
            f"Ты — эксперт-аналитик спортивных ставок. Твоя задача: кратко обосновать ставку на матч.\n\n"
            f"Матч: {m.home_team} — {m.away_team} ({m.league})\n"
            f"Ставка: {signal.outcome.value.upper()}\n"
            f"Коэффициент: {signal.bookmaker_odds:.2f}\n"
            f"Вероятность модели: {signal.model_probability:.1%}\n"
            f"Перевес (Edge): +{signal.edge:.1%}\n\n"
            f"АКТУАЛЬНЫЕ НОВОСТИ:\n{news_text}\n\n"
            f"ЗАДАНИЕ:\n"
            f"Напиши 3-4 предложения на русском языке, объясняющих, почему эта ставка выгодна.\n"
            f"Удели внимание новостям (травмы/состав), если они есть, и перевесу над линией букмекера.\n"
            f"Тон: профессиональный, сдержанный.\n"
            f"Максимальная длина: 300 символов."
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Ты эксперт-каппер, пишущий на русском языке."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 512
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.base_url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['choices'][0]['message']['content'].strip()
                    else:
                        error_text = await resp.text()
                        logger.error(f"NVIDIA API Error ({resp.status}): {error_text}")
                        return "Анализ формируется... (API NIM Busy)"
        except Exception as e:
            logger.error(f"AI Analysis failed: {e}")
            return "Ошибка связи с аналитическим модулем Llama-3."
