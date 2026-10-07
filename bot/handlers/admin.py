from datetime import timedelta
from html import escape

from aiogram import Router
from aiogram.filters import Command, Filter
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import Settings
from bot.db import repo
from bot.db.models import utcnow


class IsAdmin(Filter):
    async def __call__(self, message: Message, settings: Settings) -> bool:
        return message.from_user is not None and message.from_user.id in settings.admin_ids


def _period_block(title: str, stats: repo.PeriodStats) -> str:
    return texts.STATS_PERIOD.format(
        title=title,
        users=stats.users,
        new_users=stats.new_users,
        requests=stats.requests,
        success_rate=stats.success_rate,
    )


async def cmd_stats(message: Message, session: AsyncSession) -> None:
    now = utcnow()
    blocks = [texts.STATS_HEADER]
    for title, since in (
        ("За сутки", now - timedelta(days=1)),
        ("За неделю", now - timedelta(days=7)),
        ("За всё время", None),
    ):
        blocks.append(_period_block(title, await repo.period_stats(session, since)))

    top = await repo.top_tracks(session)
    if top:
        lines = [texts.STATS_TOP_HEADER]
        lines += [
            f"{i}. {escape(artist)} — {escape(title)} ({count})"
            for i, (title, artist, count) in enumerate(top, start=1)
        ]
        blocks.append("\n".join(lines))
    else:
        blocks.append(texts.STATS_TOP_EMPTY)
    await message.answer("\n\n".join(blocks))


def create_router() -> Router:
    router = Router(name="admin")
    router.message.register(cmd_stats, Command("stats"), IsAdmin())
    return router
