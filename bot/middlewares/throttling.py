import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot import texts
from bot.services.limits import RateLimiter

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """Не больше N запросов на распознавание в минуту от одного пользователя (FR-16).

    Суточный лимит проверяется в обработчике по таблице recognitions.
    """

    def __init__(self, limiter: RateLimiter) -> None:
        self.limiter = limiter

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)
        wait = self.limiter.hit(event.from_user.id)
        if wait:
            logger.info("Rate limit for user %s, wait %s s", event.from_user.id, wait)
            await event.reply(texts.RATE_LIMIT.format(seconds=wait))
            return None
        return await handler(event, data)
