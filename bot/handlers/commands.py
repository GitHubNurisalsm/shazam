from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot import texts
from bot.config import Settings


async def cmd_start(message: Message) -> None:
    # Регистрация пользователя в БД выполняется в UserMiddleware.
    await message.answer(texts.START)


async def cmd_help(message: Message, settings: Settings) -> None:
    await message.answer(
        texts.HELP.format(
            max_mb=settings.max_file_mb,
            per_minute=settings.rate_limit_per_minute,
            daily=settings.daily_limit,
        )
    )


async def text_hint(message: Message) -> None:
    await message.answer(texts.SEND_MEDIA_HINT)


def create_router() -> Router:
    router = Router(name="commands")
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    return router


def create_fallback_router() -> Router:
    """Подключается последним: ловит текст, который не обработали другие роутеры."""
    router = Router(name="fallback")
    router.message.register(text_hint, F.chat.type == ChatType.PRIVATE, F.text)
    return router
