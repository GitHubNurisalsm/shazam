import asyncio

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.handlers import setup_routers
from bot.middlewares.db_session import DbSessionMiddleware
from bot.middlewares.users import UserMiddleware
from bot.services.limits import RateLimiter, UserLocks
from bot.services.recognition.manager import RecognitionManager
from bot.utils import AdminAlerts


def build_dispatcher(
    bot: Bot,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    recognizer: RecognitionManager,
) -> Dispatcher:
    """Собирает диспетчер: зависимости, middlewares и роутеры."""
    dp = Dispatcher(
        settings=settings,
        recognizer=recognizer,
        user_locks=UserLocks(),
        recognition_semaphore=asyncio.Semaphore(settings.max_concurrent_recognitions),
        admin_alerts=AdminAlerts(bot, settings.admin_ids),
    )
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())
    dp.include_router(setup_routers(RateLimiter(settings.rate_limit_per_minute, period=60)))
    return dp
