import contextlib

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.db import repo
from bot.services.formatter import format_history_item


class HistoryPage(CallbackData, prefix="hist"):
    owner_id: int  # кнопки листает только владелец истории
    page: int


def render_history(page: repo.HistoryPage) -> str:
    if not page.items:
        return texts.HISTORY_EMPTY
    offset = (page.page - 1) * repo.HISTORY_PAGE_SIZE
    lines = [texts.HISTORY_HEADER.format(page=page.page, pages=page.pages)]
    for index, item in enumerate(page.items, start=offset + 1):
        date = item.created_at.strftime("%d.%m.%Y %H:%M UTC")
        lines.append(format_history_item(index, item.title or "", item.artist or "", date))
    return "\n\n".join(lines)


def history_keyboard(owner_id: int, page: repo.HistoryPage) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()
    if page.page > 1:
        builder.button(
            text=texts.HISTORY_PREV,
            callback_data=HistoryPage(owner_id=owner_id, page=page.page - 1),
        )
    if page.page < page.pages:
        builder.button(
            text=texts.HISTORY_NEXT,
            callback_data=HistoryPage(owner_id=owner_id, page=page.page + 1),
        )
    return builder.as_markup() if builder.buttons else None


async def cmd_history(message: Message, session: AsyncSession) -> None:
    assert message.from_user is not None
    owner_id = message.from_user.id
    page = await repo.get_history(session, owner_id, 1)
    await message.answer(render_history(page), reply_markup=history_keyboard(owner_id, page))


async def paginate_history(
    callback: CallbackQuery, callback_data: HistoryPage, session: AsyncSession
) -> None:
    if callback.from_user.id != callback_data.owner_id:
        await callback.answer("Это не ваша история. Отправьте /history.", show_alert=True)
        return
    page = await repo.get_history(session, callback_data.owner_id, callback_data.page)
    if callback.message is not None and isinstance(callback.message, Message):
        # TelegramBadRequest «message is not modified» — повторное нажатие той же кнопки.
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                render_history(page),
                reply_markup=history_keyboard(callback_data.owner_id, page),
            )
    await callback.answer()


async def stale_history_callback(callback: CallbackQuery) -> None:
    await callback.answer()


def create_router() -> Router:
    router = Router(name="history")
    router.message.register(cmd_history, Command("history"))
    router.callback_query.register(paginate_history, HistoryPage.filter())
    # Колбэки устаревшего формата просто гасим, чтобы у кнопки не висели «часики».
    router.callback_query.register(stale_history_callback, F.data.startswith("hist:"))
    return router
