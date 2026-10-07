import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Регистрирует пользователя, обновляет last_seen_at и молча отбрасывает забаненных."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")
        if tg_user is None or session is None or tg_user.is_bot:
            return await handler(event, data)
        user = await repo.upsert_user(
            session,
            user_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            language_code=tg_user.language_code,
        )
        if user.is_banned:
            logger.info("Ignored update from banned user %s", tg_user.id)
            return None
        data["db_user"] = user
        return await handler(event, data)
