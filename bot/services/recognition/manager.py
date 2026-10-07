import asyncio
import logging
from pathlib import Path

from bot.services.recognition.base import Recognizer, ServiceUnavailableError, TrackResult

logger = logging.getLogger(__name__)


class RecognitionManager:
    """Выбирает провайдера.

    1. Основной провайдер. Найден или «не найдено» — сразу ответ (резерв не вызываем:
       повтор почти не помогает, а запросы AudD платные).
    2. Сбой — один повтор через retry_delay.
    3. Повторный сбой — резервный провайдер, если он настроен.
    4. Резерв тоже упал — ServiceUnavailableError.
    """

    def __init__(
        self,
        primary: Recognizer,
        fallback: Recognizer | None = None,
        retry_delay: float = 1.0,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.retry_delay = retry_delay

    async def recognize(self, path: Path) -> TrackResult | None:
        for attempt in (1, 2):
            try:
                return await self.primary.recognize(path)
            except Exception as exc:
                logger.warning(
                    "Provider %s failed (attempt %d): %s", self.primary.name, attempt, exc
                )
            if attempt == 1:
                await asyncio.sleep(self.retry_delay)

        if self.fallback is None:
            raise ServiceUnavailableError(f"{self.primary.name} failed, no fallback")
        try:
            return await self.fallback.recognize(path)
        except Exception as exc:
            logger.error("Fallback provider %s failed: %s", self.fallback.name, exc)
            raise ServiceUnavailableError("all providers failed") from exc
