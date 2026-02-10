from abc import ABC, abstractmethod
from typing import List
from core.models import Match

class BaseParser(ABC):
    """
    Базовый класс для парсинга коэффициентов напрямую с API букмекеров.
    """
    @abstractmethod
    async def fetch_odds(self) -> List[Match]:
        """Получить список матчей с коэффициентами"""
        pass

    @abstractmethod
    async def close(self):
        """Закрыть сессию"""
        pass
