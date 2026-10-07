import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def tg_retry(call: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    """Повторяет вызов Bot API после flood wait (TelegramRetryAfter)."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TelegramRetryAfter as exc:
            if attempt == attempts:
                raise
            logger.warning("Flood wait %s s, retrying", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
    raise AssertionError("unreachable")


class AdminAlerts:
    """Сообщения администраторам не чаще одного раза в `interval` секунд."""

    def __init__(self, bot: Bot, admin_ids: list[int], interval: float = 600.0) -> None:
        self._bot = bot
        self._admin_ids = admin_ids
        self._interval = interval
        self._last_sent: float | None = None

    async def notify(self, text: str) -> None:
        now = time.monotonic()
        if self._last_sent is not None and now - self._last_sent < self._interval:
            return
        self._last_sent = now
        for admin_id in self._admin_ids:
            try:
                await self._bot.send_message(admin_id, text)
            except TelegramAPIError:
                logger.warning("Failed to notify admin %s", admin_id, exc_info=True)
