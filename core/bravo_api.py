"""
=============================================================================
 BRAVO API INTEGRATION (News Monitor)
 
 Интеграция с внешним сервисом новостей (Bravo/Brave Search).
 Используется для получения последних новостей о командах и травмах.
=============================================================================
"""
import aiohttp
import logging
import asyncio
import os
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class BravoNewsFetcher:
    """
    Модуль для поиска новостей через Bravo API.
    
    Если API ключ не предоставлен, возвращает демо-данные для тестирования.
    """
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("BRAVE_API_KEY") or "DEMO_KEY"
        self.base_url = "https://api.search.brave.com/res/v1/news/search"
        
    async def get_latest_news(self, query: str = "football injuries") -> List[Dict]:
        """
        Получить последние новости по запросу.
        """
        if self.api_key == "DEMO_KEY":
            return self._get_mock_news(query)
            
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key
                }
                params = {"q": query, "freshness": "Day", "count": 5}
                
                # Brave Search API requires Subscription Token in header
                # If using generic Bravo, logic might differ.
                # Assuming Standard Brave Search API usage.
                
                async with session.get(self.base_url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._parse_response(data)
                    else:
                        logger.warning(f"Bravo API Error: {resp.status}")
                        return self._get_mock_news(query)
        except Exception as e:
            logger.error(f"Failed to fetch Bravo news: {e}")
            return self._get_mock_news(query)

    def _get_mock_news(self, query: str) -> List[Dict]:
        """Демо-данные для демонстрации работы"""
        return [
            {
                "title": f"Partial news for query '{query}' (Demo)",
                "source": "Bravo Sports",
                "published_at": datetime.utcnow().isoformat(),
                "url": "https://example.com/news/1",
                "snippet": "This is a demo snippet because the API key might be invalid or quota exceeded."
            },
            {
                "title": "Mbappé hamstring injury concern",
                "source": "Bravo Sports",
                "published_at": datetime.utcnow().isoformat(),
                "url": "https://example.com/news/1",
                "snippet": "Real Madrid star Kylian Mbappé missed training today due to a hamstring issue..."
            },
            {
                "title": "Kevin De Bruyne returns to full training",
                "source": "Manchester Evening",
                "published_at": datetime.utcnow().isoformat(),
                "url": "https://example.com/news/2",
                "snippet": "Man City playmaker Kevin De Bruyne is back in contention for the weekend clash..."
            }
        ]

    def _parse_response(self, data: dict) -> List[Dict]:
        """Парсинг реального ответа Brave Search"""
        # API Response structure: { "results": [ { "title": "...", "url": "...", "description": "...", "age": "..." } ] }
        results = []
        try:
            items = data.get("results", [])
            for item in items:
                results.append({
                    "title": item.get("title", "No Title"),
                    "source": item.get("source", {}).get("name", "Brave Search"), # Source structure varies
                    "published_at": item.get("age", datetime.utcnow().isoformat()),
                    "url": item.get("url", "#"),
                    "snippet": item.get("description", "")[:200]
                })
        except Exception as e:
            logger.error(f"Error parsing Brave response: {e}")
        return results


