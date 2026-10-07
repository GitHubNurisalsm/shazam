import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

from bot import texts

logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> bool:
    """Глобальный обработчик: лог с traceback и понятное сообщение пользователю."""
    logger.error("Unhandled error in update %s", event.update.update_id, exc_info=event.exception)
    try:
        if event.update.message is not None:
            await event.update.message.answer(texts.GENERIC_ERROR)
        elif event.update.callback_query is not None:
            await event.update.callback_query.answer(texts.GENERIC_ERROR, show_alert=True)
    except TelegramAPIError:
        logger.warning("Failed to report error to user", exc_info=True)
    return True


def create_router() -> Router:
    router = Router(name="errors")
    router.errors.register(on_error)
    return router
