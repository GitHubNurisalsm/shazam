from aiogram import Router

from bot.handlers import admin, commands, errors, history, media
from bot.services.limits import RateLimiter


def setup_routers(limiter: RateLimiter) -> Router:
    root = Router(name="root")
    root.include_routers(
        errors.create_router(),
        commands.create_router(),
        history.create_router(),
        admin.create_router(),
        media.create_router(limiter),
        commands.create_fallback_router(),
    )
    return root
